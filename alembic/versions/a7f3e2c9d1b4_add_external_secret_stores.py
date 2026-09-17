"""Add organization secret stores and AWS-backed workspace secret references.

Revision ID: a7f3e2c9d1b4
Revises: 31ee4b7f175a
Create Date: 2026-09-14 19:00:00.000000

Expand-only: new tables plus nullable/defaulted columns on ``secret``. Existing
rows keep ``source='local'`` and the previous app version continues to work.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_org_optional_workspace_table_rls,
    disable_org_table_rls,
    enable_org_optional_workspace_table_rls,
    enable_org_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "a7f3e2c9d1b4"
down_revision: str | None = "31ee4b7f175a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_secret_store",
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("role_arn", sa.String(length=2048), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_organization_secret_store_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_organization_secret_store")
        ),
        sa.UniqueConstraint(
            "organization_id",
            "name",
            name=op.f("uq_organization_secret_store_organization_id"),
        ),
    )
    op.create_index(
        op.f("ix_organization_secret_store_id"),
        "organization_secret_store",
        ["id"],
        unique=True,
    )

    op.create_table(
        "workspace_secret_store_authorization",
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
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
            name=op.f(
                "fk_workspace_secret_store_authorization_organization_id_organization"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_workspace_secret_store_authorization_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["organization_secret_store.id"],
            name=op.f(
                "fk_workspace_secret_store_authorization_store_id_organization_secret_store"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_workspace_secret_store_authorization")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "store_id",
            name=op.f("uq_workspace_secret_store_authorization_workspace_id"),
        ),
    )
    op.create_index(
        op.f("ix_workspace_secret_store_authorization_id"),
        "workspace_secret_store_authorization",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_workspace_secret_store_authorization_workspace_id"),
        "workspace_secret_store_authorization",
        ["workspace_id"],
    )
    op.create_index(
        op.f("ix_workspace_secret_store_authorization_store_id"),
        "workspace_secret_store_authorization",
        ["store_id"],
    )

    op.add_column(
        "secret",
        sa.Column(
            "source",
            sa.String(length=64),
            server_default=sa.text("'local'"),
            nullable=False,
        ),
    )
    op.add_column("secret", sa.Column("store_id", sa.UUID(), nullable=True))
    op.add_column(
        "secret",
        sa.Column("remote_reference", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "secret",
        sa.Column(
            "remote_key_mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(op.f("ix_secret_store_id"), "secret", ["store_id"])
    op.create_foreign_key(
        op.f("fk_secret_store_id_organization_secret_store"),
        "secret",
        "organization_secret_store",
        ["store_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # A reference can only exist while its workspace authorization exists.
    op.create_foreign_key(
        "fk_secret_store_authorization",
        "secret",
        "workspace_secret_store_authorization",
        ["workspace_id", "store_id"],
        ["workspace_id", "store_id"],
        ondelete="RESTRICT",
    )

    op.execute(enable_org_table_rls("organization_secret_store"))
    op.execute(
        enable_org_optional_workspace_table_rls("workspace_secret_store_authorization")
    )


def downgrade() -> None:
    bind = op.get_bind()
    aws_backed = bind.execute(
        sa.text("SELECT count(*) FROM secret WHERE source <> 'local'")
    ).scalar_one()
    if aws_backed:
        raise NotImplementedError(
            "Cannot downgrade while AWS-backed workspace secret references exist. "
            "Delete the references (or restore from backup) before rolling back; "
            "they are never converted to local values."
        )

    op.execute(
        disable_org_optional_workspace_table_rls("workspace_secret_store_authorization")
    )
    op.execute(disable_org_table_rls("organization_secret_store"))

    op.drop_constraint("fk_secret_store_authorization", "secret", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_secret_store_id_organization_secret_store"),
        "secret",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_secret_store_id"), table_name="secret")
    op.drop_column("secret", "remote_key_mapping")
    op.drop_column("secret", "remote_reference")
    op.drop_column("secret", "store_id")
    op.drop_column("secret", "source")

    op.drop_index(
        op.f("ix_workspace_secret_store_authorization_store_id"),
        table_name="workspace_secret_store_authorization",
    )
    op.drop_index(
        op.f("ix_workspace_secret_store_authorization_workspace_id"),
        table_name="workspace_secret_store_authorization",
    )
    op.drop_index(
        op.f("ix_workspace_secret_store_authorization_id"),
        table_name="workspace_secret_store_authorization",
    )
    op.drop_table("workspace_secret_store_authorization")

    op.drop_index(
        op.f("ix_organization_secret_store_id"),
        table_name="organization_secret_store",
    )
    op.drop_table("organization_secret_store")
