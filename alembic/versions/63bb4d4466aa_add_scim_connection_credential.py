"""add scim connection credential

Revision ID: 63bb4d4466aa
Revises: a667d946cca1
Create Date: 2026-09-10 17:15:53.833884

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import disable_org_table_rls, enable_org_table_rls

# revision identifiers, used by Alembic.
revision: str = "63bb4d4466aa"
down_revision: str | None = "a667d946cca1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scim_connection",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("hashed", sa.String(length=128), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("preview", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            name=op.f("fk_scim_connection_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_scim_connection_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_scim_connection")),
    )
    op.create_index(
        op.f("ix_scim_connection_id"), "scim_connection", ["id"], unique=True
    )
    op.create_index(
        op.f("ix_scim_connection_key_id"), "scim_connection", ["key_id"], unique=True
    )
    # Unique, not merely indexed: one connection per organization.
    op.create_index(
        op.f("ix_scim_connection_organization_id"),
        "scim_connection",
        ["organization_id"],
        unique=True,
    )

    # Tenant isolation is enforced in the database, not only in the registry.
    op.execute(enable_org_table_rls("scim_connection"))


def downgrade() -> None:
    op.execute(disable_org_table_rls("scim_connection"))

    op.drop_index(
        op.f("ix_scim_connection_organization_id"), table_name="scim_connection"
    )
    op.drop_index(op.f("ix_scim_connection_key_id"), table_name="scim_connection")
    op.drop_index(op.f("ix_scim_connection_id"), table_name="scim_connection")
    op.drop_table("scim_connection")
