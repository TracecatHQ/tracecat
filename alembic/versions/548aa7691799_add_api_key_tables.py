"""add service account tables

Revision ID: 548aa7691799
Revises: 0c9a39e54e2f
Create Date: 2026-03-11 17:38:27.533527

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    disable_service_account_child_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_service_account_child_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "548aa7691799"
down_revision: str | None = "0c9a39e54e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_domain",
        sa.Column("verification_token", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "organization_domain",
        sa.Column("verification_record_name", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "service_account",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=True),
        sa.Column("disabled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
            "workspace_id IS NULL OR organization_id IS NOT NULL",
            name=op.f("ck_service_account_service_account_workspace_requires_org"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_service_account_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user.id"],
            name=op.f("fk_service_account_owner_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_service_account_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_service_account")),
    )
    op.create_index(
        op.f("ix_service_account_id"), "service_account", ["id"], unique=True
    )
    op.create_index(
        op.f("ix_service_account_organization_id"),
        "service_account",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_service_account_workspace_id"),
        "service_account",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "service_account_api_key",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("service_account_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("hashed", sa.String(length=128), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("preview", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
            ["created_by"],
            ["user.id"],
            name=op.f("fk_service_account_api_key_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["user.id"],
            name=op.f("fk_service_account_api_key_revoked_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"],
            ["service_account.id"],
            name=op.f("fk_service_account_api_key_service_account_id_service_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_service_account_api_key")
        ),
        sa.UniqueConstraint("key_id", name=op.f("uq_service_account_api_key_key_id")),
    )
    op.create_index(
        op.f("ix_service_account_api_key_id"),
        "service_account_api_key",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_service_account_api_key_key_id"),
        "service_account_api_key",
        ["key_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_service_account_api_key_service_account_id"),
        "service_account_api_key",
        ["service_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_service_account_api_key_active_unique",
        "service_account_api_key",
        ["service_account_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "service_account_scope",
        sa.Column("service_account_id", sa.UUID(), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scope.id"],
            name=op.f("fk_service_account_scope_scope_id_scope"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"],
            ["service_account.id"],
            name=op.f("fk_service_account_scope_service_account_id_service_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "service_account_id",
            "scope_id",
            name=op.f("pk_service_account_scope"),
        ),
    )

    op.execute(enable_org_optional_workspace_table_rls("service_account"))
    op.execute(enable_service_account_child_table_rls("service_account_api_key"))
    op.execute(enable_service_account_child_table_rls("service_account_scope"))


def downgrade() -> None:
    op.execute(disable_service_account_child_table_rls("service_account_scope"))
    op.execute(disable_service_account_child_table_rls("service_account_api_key"))
    op.execute(disable_org_optional_workspace_table_rls("service_account"))
    op.drop_table("service_account_scope")
    op.drop_index(
        "ix_service_account_api_key_active_unique",
        table_name="service_account_api_key",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index(
        op.f("ix_service_account_api_key_service_account_id"),
        table_name="service_account_api_key",
    )
    op.drop_index(
        op.f("ix_service_account_api_key_key_id"),
        table_name="service_account_api_key",
    )
    op.drop_index(
        op.f("ix_service_account_api_key_id"), table_name="service_account_api_key"
    )
    op.drop_table("service_account_api_key")
    op.drop_index(
        op.f("ix_service_account_workspace_id"),
        table_name="service_account",
    )
    op.drop_index(
        op.f("ix_service_account_organization_id"),
        table_name="service_account",
    )
    op.drop_index(op.f("ix_service_account_id"), table_name="service_account")
    op.drop_table("service_account")
    op.drop_column("organization_domain", "verification_record_name")
    op.drop_column("organization_domain", "verification_token")
