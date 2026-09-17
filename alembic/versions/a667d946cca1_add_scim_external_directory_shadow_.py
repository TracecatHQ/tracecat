"""add scim shadow tables and connection

Revision ID: a667d946cca1
Revises: e847d14eeb86
Create Date: 2026-09-10 14:07:17.735243

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import (
    disable_external_group_member_table_rls,
    disable_org_table_rls,
    enable_external_group_member_table_rls,
    enable_org_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "a667d946cca1"
down_revision: str | None = "e847d14eeb86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tenant-qualified target for the mapping's composite foreign key.
    op.create_unique_constraint(
        op.f("uq_group_id_organization_id"), "group", ["id", "organization_id"]
    )
    op.create_table(
        "external_group",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
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
            name=op.f("fk_external_group_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_group")),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name=op.f("uq_external_group_id_organization_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "external_id",
            name=op.f("uq_external_group_organization_id_external_id"),
        ),
    )
    op.create_index(
        op.f("ix_external_group_organization_id"),
        "external_group",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "external_user",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
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
            name=op.f("fk_external_user_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_external_user_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_user")),
        sa.UniqueConstraint(
            "organization_id",
            "external_id",
            name=op.f("uq_external_user_organization_id_external_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name=op.f("uq_external_user_organization_id_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_external_user_organization_id"),
        "external_user",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_external_user_user_id"), "external_user", ["user_id"], unique=False
    )
    op.create_table(
        "external_group_mapping",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("external_group_id", sa.UUID(), nullable=False),
        sa.Column("group_id", sa.UUID(), nullable=False),
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
        # Both ends must belong to the mapping's own tenant, not merely exist.
        sa.ForeignKeyConstraint(
            ["external_group_id", "organization_id"],
            ["external_group.id", "external_group.organization_id"],
            name=op.f(
                "fk_external_group_mapping_external_group_id_organization_id_external_group"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "organization_id"],
            ["group.id", "group.organization_id"],
            name=op.f("fk_external_group_mapping_group_id_organization_id_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_external_group_mapping_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_group_mapping")),
        sa.UniqueConstraint(
            "external_group_id",
            "group_id",
            name=op.f("uq_external_group_mapping_external_group_id_group_id"),
        ),
    )
    op.create_index(
        op.f("ix_external_group_mapping_external_group_id"),
        "external_group_mapping",
        ["external_group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_external_group_mapping_group_id"),
        "external_group_mapping",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_external_group_mapping_organization_id"),
        "external_group_mapping",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "external_group_member",
        sa.Column("external_group_id", sa.UUID(), nullable=False),
        sa.Column("external_user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["external_group_id"],
            ["external_group.id"],
            name=op.f("fk_external_group_member_external_group_id_external_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["external_user_id"],
            ["external_user.id"],
            name=op.f("fk_external_group_member_external_user_id_external_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "external_group_id",
            "external_user_id",
            name=op.f("pk_external_group_member"),
        ),
    )
    # The PK leads with the group, so member-side lookups need their own index.
    op.create_index(
        op.f("ix_external_group_member_external_user_id"),
        "external_group_member",
        ["external_user_id"],
        unique=False,
    )

    # Tenant isolation is enforced in the database, not only in the registry.
    for table in ("external_user", "external_group", "external_group_mapping"):
        op.execute(enable_org_table_rls(table))
    op.execute(enable_external_group_member_table_rls())

    op.create_table(
        "scim_connection",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("hashed", sa.String(length=128), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("preview", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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

    op.execute(disable_external_group_member_table_rls())
    for table in ("external_group_mapping", "external_group", "external_user"):
        op.execute(disable_org_table_rls(table))

    op.drop_index(
        op.f("ix_external_group_member_external_user_id"),
        table_name="external_group_member",
    )
    op.drop_table("external_group_member")
    op.drop_index(
        op.f("ix_external_group_mapping_organization_id"),
        table_name="external_group_mapping",
    )
    op.drop_index(
        op.f("ix_external_group_mapping_group_id"), table_name="external_group_mapping"
    )
    op.drop_index(
        op.f("ix_external_group_mapping_external_group_id"),
        table_name="external_group_mapping",
    )
    op.drop_table("external_group_mapping")
    op.drop_index(op.f("ix_external_user_user_id"), table_name="external_user")
    op.drop_index(op.f("ix_external_user_organization_id"), table_name="external_user")
    op.drop_table("external_user")
    op.drop_index(
        op.f("ix_external_group_organization_id"), table_name="external_group"
    )
    op.drop_table("external_group")
    op.drop_constraint(op.f("uq_group_id_organization_id"), "group", type_="unique")
