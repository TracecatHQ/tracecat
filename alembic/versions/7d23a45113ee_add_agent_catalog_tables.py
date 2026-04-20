"""add agent catalog tables

Revision ID: b742858f7d69
Revises: 0c9a39e54e2f
Create Date: 2026-04-08 13:17:30.844855

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_agent_catalog_table_rls,
    disable_org_optional_workspace_table_rls,
    disable_org_table_rls,
    enable_agent_catalog_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_org_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "7d23a45113ee"
down_revision: str | None = "0c9a39e54e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_custom_provider",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column(
            "passthrough",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("encrypted_config", sa.LargeBinary(), nullable=True),
        sa.Column("api_key_header", sa.String(length=120), nullable=True),
        sa.Column("last_refreshed_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            name=op.f("fk_agent_custom_provider_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_custom_provider")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_agent_custom_provider_organization_id_id"),
        ),
    )
    op.create_index(
        op.f("ix_agent_custom_provider_id"),
        "agent_custom_provider",
        ["id"],
        unique=True,
    )

    op.create_table(
        "agent_catalog",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("custom_provider_id", sa.UUID(), nullable=True),
        sa.Column("model_provider", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=500), nullable=False),
        sa.Column(
            "model_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("encrypted_config", sa.LargeBinary(), nullable=True),
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
            "custom_provider_id IS NULL OR organization_id IS NOT NULL",
            name=op.f("custom_provider_requires_org"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "custom_provider_id"],
            ["agent_custom_provider.organization_id", "agent_custom_provider.id"],
            name="fk_agent_catalog_org_custom_provider",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_catalog_organization_id_organization"),
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
        "ix_agent_catalog_organization_id_custom_provider_id",
        "agent_catalog",
        ["organization_id", "custom_provider_id"],
        unique=False,
    )
    op.create_index(
        "uq_agent_catalog_custom_provider_model_provider_model_name",
        "agent_catalog",
        ["organization_id", "custom_provider_id", "model_provider", "model_name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "agent_model_access",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("catalog_id", sa.UUID(), nullable=False),
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
            ["catalog_id"],
            ["agent_catalog.id"],
            name=op.f("fk_agent_model_access_catalog_id_agent_catalog"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_model_access_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_model_access_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_model_access")),
    )
    op.create_index(
        "ix_agent_model_access_catalog_id",
        "agent_model_access",
        ["catalog_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_model_access_id"),
        "agent_model_access",
        ["id"],
        unique=True,
    )
    op.create_index(
        "ix_agent_model_access_workspace_id",
        "agent_model_access",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_agent_model_access_organization_workspace_catalog",
        "agent_model_access",
        ["organization_id", "workspace_id", "catalog_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.add_column("agent_preset", sa.Column("catalog_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_agent_preset_catalog_id"),
        "agent_preset",
        ["catalog_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_agent_preset_catalog_id_agent_catalog"),
        "agent_preset",
        "agent_catalog",
        ["catalog_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "agent_preset_version",
        sa.Column("catalog_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_preset_version_catalog_id"),
        "agent_preset_version",
        ["catalog_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_agent_preset_version_catalog_id_agent_catalog"),
        "agent_preset_version",
        "agent_catalog",
        ["catalog_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(enable_org_table_rls("agent_custom_provider"))
    op.execute(enable_agent_catalog_table_rls())
    op.execute(enable_org_optional_workspace_table_rls("agent_model_access"))


def downgrade() -> None:
    op.execute(disable_org_optional_workspace_table_rls("agent_model_access"))
    op.execute(disable_agent_catalog_table_rls())
    op.execute(disable_org_table_rls("agent_custom_provider"))

    op.drop_constraint(
        op.f("fk_agent_preset_version_catalog_id_agent_catalog"),
        "agent_preset_version",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_agent_preset_version_catalog_id"),
        table_name="agent_preset_version",
    )
    op.drop_column("agent_preset_version", "catalog_id")

    op.drop_constraint(
        op.f("fk_agent_preset_catalog_id_agent_catalog"),
        "agent_preset",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_agent_preset_catalog_id"), table_name="agent_preset")
    op.drop_column("agent_preset", "catalog_id")

    op.drop_index(
        "uq_agent_model_access_organization_workspace_catalog",
        table_name="agent_model_access",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index("ix_agent_model_access_workspace_id", table_name="agent_model_access")
    op.drop_index(op.f("ix_agent_model_access_id"), table_name="agent_model_access")
    op.drop_index("ix_agent_model_access_catalog_id", table_name="agent_model_access")
    op.drop_table("agent_model_access")

    op.drop_index(
        "uq_agent_catalog_custom_provider_model_provider_model_name",
        table_name="agent_catalog",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index(
        "ix_agent_catalog_organization_id_custom_provider_id",
        table_name="agent_catalog",
    )
    op.drop_index(op.f("ix_agent_catalog_organization_id"), table_name="agent_catalog")
    op.drop_table("agent_catalog")

    op.drop_index(
        op.f("ix_agent_custom_provider_id"), table_name="agent_custom_provider"
    )
    op.drop_table("agent_custom_provider")
