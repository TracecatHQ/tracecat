"""add api key tables

Revision ID: 548aa7691799
Revises: 0a1e3100a432
Create Date: 2026-03-11 17:38:27.533527

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    disable_org_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_org_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "548aa7691799"
down_revision: str | None = "0a1e3100a432"
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
        "organization_api_key",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("hashed", sa.String(length=128), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("preview", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_organization_api_key_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_organization_api_key_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["user.id"],
            name=op.f("fk_organization_api_key_revoked_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_organization_api_key")),
        sa.UniqueConstraint("key_id", name=op.f("uq_organization_api_key_key_id")),
    )
    op.create_index(
        op.f("ix_organization_api_key_id"),
        "organization_api_key",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_organization_api_key_organization_id"),
        "organization_api_key",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "workspace_api_key",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("hashed", sa.String(length=128), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("preview", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_workspace_api_key_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_workspace_api_key_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["user.id"],
            name=op.f("fk_workspace_api_key_revoked_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_workspace_api_key_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_workspace_api_key")),
        sa.UniqueConstraint("key_id", name=op.f("uq_workspace_api_key_key_id")),
    )
    op.create_index(
        op.f("ix_workspace_api_key_id"),
        "workspace_api_key",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_api_key_organization_id"),
        "workspace_api_key",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_api_key_workspace_id"),
        "workspace_api_key",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "organization_api_key_scope",
        sa.Column("api_key_id", sa.UUID(), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["organization_api_key.id"],
            name=op.f("fk_organization_api_key_scope_api_key_id_organization_api_key"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scope.id"],
            name=op.f("fk_organization_api_key_scope_scope_id_scope"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "api_key_id",
            "scope_id",
            name=op.f("pk_organization_api_key_scope"),
        ),
    )

    op.create_table(
        "workspace_api_key_scope",
        sa.Column("api_key_id", sa.UUID(), nullable=False),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["workspace_api_key.id"],
            name=op.f("fk_workspace_api_key_scope_api_key_id_workspace_api_key"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scope.id"],
            name=op.f("fk_workspace_api_key_scope_scope_id_scope"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "api_key_id",
            "scope_id",
            name=op.f("pk_workspace_api_key_scope"),
        ),
    )

    op.execute(enable_org_table_rls("organization_api_key"))
    op.execute(enable_org_optional_workspace_table_rls("workspace_api_key"))


def downgrade() -> None:
    op.execute(disable_org_optional_workspace_table_rls("workspace_api_key"))
    op.execute(disable_org_table_rls("organization_api_key"))
    op.drop_table("workspace_api_key_scope")
    op.drop_table("organization_api_key_scope")
    op.drop_index(
        op.f("ix_workspace_api_key_workspace_id"), table_name="workspace_api_key"
    )
    op.drop_index(
        op.f("ix_workspace_api_key_organization_id"),
        table_name="workspace_api_key",
    )
    op.drop_index(op.f("ix_workspace_api_key_id"), table_name="workspace_api_key")
    op.drop_table("workspace_api_key")
    op.drop_index(
        op.f("ix_organization_api_key_organization_id"),
        table_name="organization_api_key",
    )
    op.drop_index(
        op.f("ix_organization_api_key_id"),
        table_name="organization_api_key",
    )
    op.drop_table("organization_api_key")
    op.drop_column("organization_domain", "verification_record_name")
    op.drop_column("organization_domain", "verification_token")
