"""Add tier and organization_tier tables

Revision ID: a1b2c3d4e5f7
Revises: f4f5b93cbb16
Create Date: 2025-01-23 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "f4f5b93cbb16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create tier table with UUID primary key
    op.create_table(
        "tier",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("max_concurrent_workflows", sa.Integer(), nullable=True),
        sa.Column("max_action_executions_per_workflow", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_actions", sa.Integer(), nullable=True),
        sa.Column("api_rate_limit", sa.Integer(), nullable=True),
        sa.Column("api_burst_capacity", sa.Integer(), nullable=True),
        sa.Column(
            "entitlements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.PrimaryKeyConstraint("id", name="pk_tier"),
    )

    # Seed the default tier (unlimited everything for self-hosted)
    op.execute(
        """
        INSERT INTO tier (id, display_name, max_concurrent_workflows, max_action_executions_per_workflow,
                          max_concurrent_actions, api_rate_limit, api_burst_capacity, entitlements,
                          is_default, sort_order, is_active)
        VALUES (
            gen_random_uuid(),
            'Default',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            '{"custom_registry": true, "sso": true, "git_sync": true}'::jsonb,
            true,
            0,
            true
        )
        """
    )

    # Create organization_tier table
    op.create_table(
        "organization_tier",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tier_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("max_concurrent_workflows", sa.Integer(), nullable=True),
        sa.Column("max_action_executions_per_workflow", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_actions", sa.Integer(), nullable=True),
        sa.Column("api_rate_limit", sa.Integer(), nullable=True),
        sa.Column("api_burst_capacity", sa.Integer(), nullable=True),
        sa.Column(
            "entitlement_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_organization_tier"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_organization_tier_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tier_id"],
            ["tier.id"],
            name="fk_organization_tier_tier_id_tier",
        ),
        sa.UniqueConstraint(
            "organization_id", name="uq_organization_tier_organization_id"
        ),
    )

    # Backfill: Create organization_tier records for all existing organizations
    op.execute(
        """
        INSERT INTO organization_tier (id, organization_id, tier_id)
        SELECT gen_random_uuid(), o.id, t.id
        FROM organization o
        CROSS JOIN tier t
        WHERE t.is_default = true
          AND o.id NOT IN (SELECT organization_id FROM organization_tier)
        """
    )


def downgrade() -> None:
    op.drop_table("organization_tier")
    op.drop_table("tier")
