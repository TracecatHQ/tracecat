"""llm provider v2

Revision ID: 3fbfa9cc5a08
Revises: 6171727be56a
Create Date: 2026-03-12 13:24:48.559701

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    disable_org_shared_table_rls,
    disable_org_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_org_shared_table_rls,
    enable_org_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "3fbfa9cc5a08"
down_revision: str | None = "6171727be56a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENABLE_ALL_MODELS_ON_UPGRADE_SETTING = "agent_enable_all_models_on_upgrade"


def upgrade() -> None:
    op.create_table(
        "agent_custom_sources",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("model_provider", sa.String(length=120), nullable=True),
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
            name=op.f("fk_agent_custom_sources_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_custom_sources")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_agent_custom_sources_organization_id_id"),
        ),
    )
    op.create_index(
        op.f("ix_agent_custom_sources_id"),
        "agent_custom_sources",
        ["id"],
        unique=True,
    )

    op.create_table(
        "agent_catalog",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("model_provider", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=500), nullable=False),
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
        sa.CheckConstraint(
            "(source_id IS NULL AND organization_id IS NULL) "
            "OR (source_id IS NOT NULL AND organization_id IS NOT NULL)",
            name=op.f("ck_agent_catalog_ck_agent_catalog_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_catalog_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["agent_custom_sources.id"],
            name=op.f("fk_agent_catalog_source_id_agent_custom_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_catalog")),
    )
    op.create_index(
        op.f("ix_agent_catalog_organization_id"),
        "agent_catalog",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_catalog_organization_id_source_id",
        "agent_catalog",
        ["organization_id", "source_id"],
        unique=False,
    )
    op.create_index(
        "uq_agent_catalog_source_id_model_provider_model_name",
        "agent_catalog",
        ["source_id", "model_provider", "model_name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "agent_enabled_models",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("model_provider", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=500), nullable=False),
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
        sa.CheckConstraint(
            "workspace_id IS NULL OR enabled_config IS NULL",
            name=op.f(
                "ck_agent_enabled_models_ck_agent_enabled_models_workspace_config"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_enabled_models_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["agent_custom_sources.id"],
            name=op.f("fk_agent_enabled_models_source_id_agent_custom_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_enabled_models_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_enabled_models")),
    )
    op.create_index(
        op.f("ix_agent_enabled_models_id"),
        "agent_enabled_models",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_enabled_models_source_id"),
        "agent_enabled_models",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_enabled_models_workspace_id",
        "agent_enabled_models",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_agent_enabled_models_identity",
        "agent_enabled_models",
        [
            "organization_id",
            "workspace_id",
            "source_id",
            "model_provider",
            "model_name",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.add_column("agent_preset", sa.Column("source_id", sa.UUID(), nullable=True))
    op.alter_column(
        "agent_preset",
        "model_name",
        existing_type=sa.VARCHAR(length=120),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
    op.create_index(
        op.f("ix_agent_preset_source_id"), "agent_preset", ["source_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_agent_preset_source_id_agent_custom_sources"),
        "agent_preset",
        "agent_custom_sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "agent_preset_version", sa.Column("source_id", sa.UUID(), nullable=True)
    )
    op.alter_column(
        "agent_preset_version",
        "model_name",
        existing_type=sa.VARCHAR(length=120),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
    op.create_index(
        op.f("ix_agent_preset_version_source_id"),
        "agent_preset_version",
        ["source_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_agent_preset_version_source_id_agent_custom_sources"),
        "agent_preset_version",
        "agent_custom_sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("agent_session", sa.Column("source_id", sa.UUID(), nullable=True))
    op.add_column(
        "agent_session", sa.Column("model_name", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "agent_session",
        sa.Column("model_provider", sa.String(length=120), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_session_source_id"), "agent_session", ["source_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_agent_session_source_id_agent_custom_sources"),
        "agent_session",
        "agent_custom_sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(enable_org_table_rls("agent_custom_sources"))
    op.execute(enable_org_shared_table_rls("agent_catalog"))
    op.execute(enable_org_optional_workspace_table_rls("agent_enabled_models"))

    bind = op.get_bind()
    organization_ids = [
        row[0]
        for row in bind.execute(
            sa.select(sa.column("id")).select_from(sa.table("organization"))
        )
    ]
    if organization_ids:
        organization_settings = sa.table(
            "organization_settings",
            sa.column("organization_id", sa.UUID()),
            sa.column("id", sa.UUID()),
            sa.column("key", sa.String()),
            sa.column("value", sa.LargeBinary()),
            sa.column("value_type", sa.String()),
            sa.column("is_encrypted", sa.Boolean()),
        )
        op.execute(
            postgresql.insert(organization_settings)
            .values(
                [
                    {
                        "organization_id": organization_id,
                        "id": uuid.uuid4(),
                        "key": ENABLE_ALL_MODELS_ON_UPGRADE_SETTING,
                        "value": b"true",
                        "value_type": "json",
                        "is_encrypted": False,
                    }
                    for organization_id in organization_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "key"],
            )
        )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM organization_settings WHERE key = :key").bindparams(
            key=ENABLE_ALL_MODELS_ON_UPGRADE_SETTING
        )
    )
    op.execute(disable_org_optional_workspace_table_rls("agent_enabled_models"))
    op.execute(disable_org_shared_table_rls("agent_catalog"))
    op.execute(disable_org_table_rls("agent_custom_sources"))

    op.drop_constraint(
        op.f("fk_agent_session_source_id_agent_custom_sources"),
        "agent_session",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_agent_session_source_id"), table_name="agent_session")
    op.drop_column("agent_session", "model_provider")
    op.drop_column("agent_session", "model_name")
    op.drop_column("agent_session", "source_id")

    op.drop_constraint(
        op.f("fk_agent_preset_version_source_id_agent_custom_sources"),
        "agent_preset_version",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_agent_preset_version_source_id"), table_name="agent_preset_version"
    )
    op.alter_column(
        "agent_preset_version",
        "model_name",
        existing_type=sa.String(length=500),
        type_=sa.VARCHAR(length=120),
        existing_nullable=False,
    )
    op.drop_column("agent_preset_version", "source_id")

    op.drop_constraint(
        op.f("fk_agent_preset_source_id_agent_custom_sources"),
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_agent_preset_source_id"), table_name="agent_preset")
    op.alter_column(
        "agent_preset",
        "model_name",
        existing_type=sa.String(length=500),
        type_=sa.VARCHAR(length=120),
        existing_nullable=False,
    )
    op.drop_column("agent_preset", "source_id")

    op.drop_index(
        "uq_agent_enabled_models_identity",
        table_name="agent_enabled_models",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index(
        "ix_agent_enabled_models_workspace_id", table_name="agent_enabled_models"
    )
    op.drop_index(
        op.f("ix_agent_enabled_models_source_id"), table_name="agent_enabled_models"
    )
    op.drop_index(op.f("ix_agent_enabled_models_id"), table_name="agent_enabled_models")
    op.drop_table("agent_enabled_models")

    op.drop_index(
        "uq_agent_catalog_source_id_model_provider_model_name",
        table_name="agent_catalog",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index(
        "ix_agent_catalog_organization_id_source_id", table_name="agent_catalog"
    )
    op.drop_index(op.f("ix_agent_catalog_organization_id"), table_name="agent_catalog")
    op.drop_table("agent_catalog")

    op.drop_index(
        op.f("ix_agent_custom_sources_id"),
        table_name="agent_custom_sources",
    )
    op.drop_table("agent_custom_sources")
