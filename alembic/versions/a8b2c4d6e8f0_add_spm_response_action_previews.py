"""add spm response action previews

Revision ID: a8b2c4d6e8f0
Revises: f3a9c2d4e6b1
Create Date: 2026-05-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import disable_org_table_rls, enable_org_table_rls

# revision identifiers, used by Alembic.
revision: str = "a8b2c4d6e8f0"
down_revision: str | None = "f3a9c2d4e6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spm_response_action_preview",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("endpoint_id", sa.UUID(), nullable=False),
        sa.Column("finding_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column("target_path", sa.String(length=1024), nullable=True),
        sa.Column("before_content", sa.Text(), nullable=True),
        sa.Column("after_content", sa.Text(), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
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
            ["endpoint_id"],
            ["spm_endpoint.id"],
            name=op.f("fk_spm_response_action_preview_endpoint_id_spm_endpoint"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["spm_finding.id"],
            name=op.f("fk_spm_response_action_preview_finding_id_spm_finding"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_response_action_preview_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["user.id"],
            name=op.f("fk_spm_response_action_preview_requested_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_spm_response_action_preview")
        ),
    )
    op.create_index(
        op.f("ix_spm_response_action_preview_id"),
        "spm_response_action_preview",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_spm_response_action_preview_endpoint_id"),
        "spm_response_action_preview",
        ["endpoint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_spm_response_action_preview_finding_id"),
        "spm_response_action_preview",
        ["finding_id"],
        unique=False,
    )
    op.create_index(
        "ix_spm_preview_org_created",
        "spm_response_action_preview",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_spm_preview_org_endpoint",
        "spm_response_action_preview",
        ["organization_id", "endpoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_spm_preview_org_status",
        "spm_response_action_preview",
        ["organization_id", "status"],
        unique=False,
    )
    op.execute(enable_org_table_rls("spm_response_action_preview"))


def downgrade() -> None:
    op.execute(disable_org_table_rls("spm_response_action_preview"))
    op.drop_index("ix_spm_preview_org_status", table_name="spm_response_action_preview")
    op.drop_index(
        "ix_spm_preview_org_endpoint", table_name="spm_response_action_preview"
    )
    op.drop_index(
        "ix_spm_preview_org_created", table_name="spm_response_action_preview"
    )
    op.drop_index(
        op.f("ix_spm_response_action_preview_finding_id"),
        table_name="spm_response_action_preview",
    )
    op.drop_index(
        op.f("ix_spm_response_action_preview_endpoint_id"),
        table_name="spm_response_action_preview",
    )
    op.drop_index(
        op.f("ix_spm_response_action_preview_id"),
        table_name="spm_response_action_preview",
    )
    op.drop_table("spm_response_action_preview")
