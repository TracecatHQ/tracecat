"""add scim external directory shadow tables

Revision ID: a667d946cca1
Revises: 31ee4b7f175a
Create Date: 2026-09-10 14:07:17.735243

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import disable_org_table_rls, enable_org_table_rls

# revision identifiers, used by Alembic.
revision: str = "a667d946cca1"
down_revision: str | None = "31ee4b7f175a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sa.Enum("MANUAL", "SCIM", name="groupmembersource").create(op.get_bind())
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
        sa.ForeignKeyConstraint(
            ["external_group_id"],
            ["external_group.id"],
            name=op.f("fk_external_group_mapping_external_group_id_external_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["group.id"],
            name=op.f("fk_external_group_mapping_group_id_group"),
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
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["external_group_id"],
            ["external_group.id"],
            name=op.f("fk_external_group_member_external_group_id_external_group"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_external_group_member_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "external_group_id", "user_id", name=op.f("pk_external_group_member")
        ),
    )
    # Existing rows predate the projection, so they are manual by definition.
    op.add_column(
        "group_member",
        sa.Column(
            "source",
            postgresql.ENUM(
                "MANUAL", "SCIM", name="groupmembersource", create_type=False
            ),
            server_default="MANUAL",
            nullable=False,
        ),
    )

    # Tenant isolation is enforced in the database, not only in the registry.
    for table in ("external_user", "external_group", "external_group_mapping"):
        op.execute(enable_org_table_rls(table))


def downgrade() -> None:
    for table in ("external_group_mapping", "external_group", "external_user"):
        op.execute(disable_org_table_rls(table))

    op.drop_column("group_member", "source")
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
    sa.Enum("MANUAL", "SCIM", name="groupmembersource").drop(op.get_bind())
