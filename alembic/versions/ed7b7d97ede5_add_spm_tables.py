"""add spm tables

Revision ID: ed7b7d97ede5
Revises: 0c9a39e54e2f
Create Date: 2026-04-22 14:04:45.501239

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
revision: str = "ed7b7d97ede5"
down_revision: str | None = "0c9a39e54e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spm_endpoint",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("os_user", sa.String(length=255), nullable=True),
        sa.Column("home_path", sa.String(length=500), nullable=True),
        sa.Column("endpoint_version", sa.String(length=64), nullable=True),
        sa.Column(
            "client_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("enrollment_token_hash", sa.String(length=64), nullable=True),
        sa.Column("endpoint_secret_hash", sa.String(length=64), nullable=True),
        sa.Column("enrolled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
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
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_endpoint_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_spm_endpoint")),
    )
    op.create_index(op.f("ix_spm_endpoint_id"), "spm_endpoint", ["id"], unique=True)
    op.create_index(
        "ix_spm_endpoint_org_last_seen",
        "spm_endpoint",
        ["organization_id", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_spm_endpoint_org_status",
        "spm_endpoint",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_spm_endpoint_org_updated",
        "spm_endpoint",
        ["organization_id", "updated_at"],
        unique=False,
    )
    op.execute(enable_org_table_rls("spm_endpoint"))

    op.create_table(
        "spm_asset",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("asset_class", sa.String(length=64), nullable=False),
        sa.Column("asset_type", sa.String(length=64), nullable=False),
        sa.Column("identity_key", sa.String(length=500), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_asset_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_spm_asset")),
        sa.UniqueConstraint(
            "organization_id",
            "harness",
            "asset_class",
            "asset_type",
            "identity_key",
            name="uq_spm_asset_org_identity",
        ),
    )
    op.create_index(op.f("ix_spm_asset_id"), "spm_asset", ["id"], unique=True)
    op.create_index(
        "ix_spm_asset_org_harness_class_type",
        "spm_asset",
        ["organization_id", "harness", "asset_class", "asset_type"],
        unique=False,
    )
    op.create_index(
        "ix_spm_asset_org_last_seen",
        "spm_asset",
        ["organization_id", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_spm_asset_org_updated",
        "spm_asset",
        ["organization_id", "updated_at"],
        unique=False,
    )
    op.execute(enable_org_table_rls("spm_asset"))

    op.create_table(
        "spm_asset_sighting",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("endpoint_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "observed_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
            ["asset_id"],
            ["spm_asset.id"],
            name=op.f("fk_spm_asset_sighting_asset_id_spm_asset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["spm_endpoint.id"],
            name=op.f("fk_spm_asset_sighting_endpoint_id_spm_endpoint"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_asset_sighting_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_spm_asset_sighting_workspace_id_workspace"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_spm_asset_sighting")),
        sa.UniqueConstraint(
            "organization_id",
            "endpoint_id",
            "asset_id",
            name="uq_spm_asset_sighting_endpoint_asset",
        ),
    )
    op.create_index(
        op.f("ix_spm_asset_sighting_asset_id"),
        "spm_asset_sighting",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_spm_asset_sighting_endpoint_id"),
        "spm_asset_sighting",
        ["endpoint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_spm_asset_sighting_id"),
        "spm_asset_sighting",
        ["id"],
        unique=True,
    )
    op.create_index(
        "ix_spm_asset_sighting_org_seen",
        "spm_asset_sighting",
        ["organization_id", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_spm_asset_sighting_org_workspace_seen",
        "spm_asset_sighting",
        ["organization_id", "workspace_id", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_spm_asset_sighting_workspace_id"),
        "spm_asset_sighting",
        ["workspace_id"],
        unique=False,
    )
    op.execute(enable_org_optional_workspace_table_rls("spm_asset_sighting"))

    op.create_table(
        "spm_finding",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("endpoint_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("asset_sighting_id", sa.UUID(), nullable=True),
        sa.Column("control_id", sa.String(length=255), nullable=False),
        sa.Column("control_revision", sa.String(length=64), nullable=True),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("asset_class", sa.String(length=64), nullable=False),
        sa.Column("asset_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "enrichment",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("recommended_action", sa.String(length=64), nullable=True),
        sa.Column(
            "recommended_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "opened_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_decision_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            ["asset_id"],
            ["spm_asset.id"],
            name=op.f("fk_spm_finding_asset_id_spm_asset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_sighting_id"],
            ["spm_asset_sighting.id"],
            name=op.f("fk_spm_finding_asset_sighting_id_spm_asset_sighting"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["spm_endpoint.id"],
            name=op.f("fk_spm_finding_endpoint_id_spm_endpoint"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_finding_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_spm_finding")),
        sa.UniqueConstraint(
            "organization_id",
            "endpoint_id",
            "asset_id",
            "control_id",
            name="uq_spm_finding_endpoint_asset_control",
        ),
    )
    op.create_index(op.f("ix_spm_finding_id"), "spm_finding", ["id"], unique=True)
    op.create_index(
        op.f("ix_spm_finding_asset_id"), "spm_finding", ["asset_id"], unique=False
    )
    op.create_index(
        op.f("ix_spm_finding_asset_sighting_id"),
        "spm_finding",
        ["asset_sighting_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_spm_finding_endpoint_id"),
        "spm_finding",
        ["endpoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_spm_finding_org_endpoint",
        "spm_finding",
        ["organization_id", "endpoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_spm_finding_org_status",
        "spm_finding",
        ["organization_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_spm_finding_org_updated",
        "spm_finding",
        ["organization_id", "updated_at"],
        unique=False,
    )
    op.execute(enable_org_table_rls("spm_finding"))

    op.create_table(
        "spm_finding_decision",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("finding_id", sa.UUID(), nullable=False),
        sa.Column("endpoint_id", sa.UUID(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
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
            ["decided_by_user_id"],
            ["user.id"],
            name=op.f("fk_spm_finding_decision_decided_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["spm_endpoint.id"],
            name=op.f("fk_spm_finding_decision_endpoint_id_spm_endpoint"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["spm_finding.id"],
            name=op.f("fk_spm_finding_decision_finding_id_spm_finding"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_finding_decision_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_spm_finding_decision")),
    )
    op.create_index(
        op.f("ix_spm_finding_decision_id"),
        "spm_finding_decision",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_spm_finding_decision_endpoint_id"),
        "spm_finding_decision",
        ["endpoint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_spm_finding_decision_finding_id"),
        "spm_finding_decision",
        ["finding_id"],
        unique=False,
    )
    op.create_index(
        "ix_spm_finding_decision_org_created",
        "spm_finding_decision",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_spm_finding_decision_org_finding",
        "spm_finding_decision",
        ["organization_id", "finding_id"],
        unique=False,
    )
    op.execute(enable_org_table_rls("spm_finding_decision"))

    op.create_table(
        "spm_enforcement_task",
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
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
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
            name=op.f("fk_spm_enforcement_task_endpoint_id_spm_endpoint"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["spm_finding.id"],
            name=op.f("fk_spm_enforcement_task_finding_id_spm_finding"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_spm_enforcement_task_organization_id_organization"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["user.id"],
            name=op.f("fk_spm_enforcement_task_requested_by_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_spm_enforcement_task")),
    )
    op.create_index(
        op.f("ix_spm_enforcement_task_id"),
        "spm_enforcement_task",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_spm_enforcement_task_endpoint_id"),
        "spm_enforcement_task",
        ["endpoint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_spm_enforcement_task_finding_id"),
        "spm_enforcement_task",
        ["finding_id"],
        unique=False,
    )
    op.create_index(
        "ix_spm_task_org_created",
        "spm_enforcement_task",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_spm_task_org_endpoint",
        "spm_enforcement_task",
        ["organization_id", "endpoint_id"],
        unique=False,
    )
    op.create_index(
        "ix_spm_task_org_status",
        "spm_enforcement_task",
        ["organization_id", "status"],
        unique=False,
    )
    op.execute(enable_org_table_rls("spm_enforcement_task"))


def downgrade() -> None:
    op.execute(disable_org_table_rls("spm_enforcement_task"))
    op.drop_index("ix_spm_task_org_status", table_name="spm_enforcement_task")
    op.drop_index("ix_spm_task_org_endpoint", table_name="spm_enforcement_task")
    op.drop_index("ix_spm_task_org_created", table_name="spm_enforcement_task")
    op.drop_index(
        op.f("ix_spm_enforcement_task_finding_id"),
        table_name="spm_enforcement_task",
    )
    op.drop_index(
        op.f("ix_spm_enforcement_task_endpoint_id"),
        table_name="spm_enforcement_task",
    )
    op.drop_index(
        op.f("ix_spm_enforcement_task_id"),
        table_name="spm_enforcement_task",
    )
    op.drop_table("spm_enforcement_task")

    op.execute(disable_org_table_rls("spm_finding_decision"))
    op.drop_index(
        "ix_spm_finding_decision_org_finding", table_name="spm_finding_decision"
    )
    op.drop_index(
        "ix_spm_finding_decision_org_created", table_name="spm_finding_decision"
    )
    op.drop_index(
        op.f("ix_spm_finding_decision_finding_id"),
        table_name="spm_finding_decision",
    )
    op.drop_index(
        op.f("ix_spm_finding_decision_endpoint_id"),
        table_name="spm_finding_decision",
    )
    op.drop_index(
        op.f("ix_spm_finding_decision_id"),
        table_name="spm_finding_decision",
    )
    op.drop_table("spm_finding_decision")

    op.execute(disable_org_table_rls("spm_finding"))
    op.drop_index("ix_spm_finding_org_updated", table_name="spm_finding")
    op.drop_index("ix_spm_finding_org_status", table_name="spm_finding")
    op.drop_index("ix_spm_finding_org_endpoint", table_name="spm_finding")
    op.drop_index(op.f("ix_spm_finding_endpoint_id"), table_name="spm_finding")
    op.drop_index(op.f("ix_spm_finding_asset_sighting_id"), table_name="spm_finding")
    op.drop_index(op.f("ix_spm_finding_asset_id"), table_name="spm_finding")
    op.drop_index(op.f("ix_spm_finding_id"), table_name="spm_finding")
    op.drop_table("spm_finding")

    op.execute(disable_org_optional_workspace_table_rls("spm_asset_sighting"))
    op.drop_index(
        op.f("ix_spm_asset_sighting_workspace_id"),
        table_name="spm_asset_sighting",
    )
    op.drop_index(
        "ix_spm_asset_sighting_org_workspace_seen",
        table_name="spm_asset_sighting",
    )
    op.drop_index("ix_spm_asset_sighting_org_seen", table_name="spm_asset_sighting")
    op.drop_index(op.f("ix_spm_asset_sighting_id"), table_name="spm_asset_sighting")
    op.drop_index(
        op.f("ix_spm_asset_sighting_endpoint_id"),
        table_name="spm_asset_sighting",
    )
    op.drop_index(
        op.f("ix_spm_asset_sighting_asset_id"), table_name="spm_asset_sighting"
    )
    op.drop_table("spm_asset_sighting")

    op.execute(disable_org_table_rls("spm_asset"))
    op.drop_index("ix_spm_asset_org_updated", table_name="spm_asset")
    op.drop_index("ix_spm_asset_org_last_seen", table_name="spm_asset")
    op.drop_index("ix_spm_asset_org_harness_class_type", table_name="spm_asset")
    op.drop_index(op.f("ix_spm_asset_id"), table_name="spm_asset")
    op.drop_table("spm_asset")

    op.execute(disable_org_table_rls("spm_endpoint"))
    op.drop_index("ix_spm_endpoint_org_updated", table_name="spm_endpoint")
    op.drop_index("ix_spm_endpoint_org_status", table_name="spm_endpoint")
    op.drop_index("ix_spm_endpoint_org_last_seen", table_name="spm_endpoint")
    op.drop_index(op.f("ix_spm_endpoint_id"), table_name="spm_endpoint")
    op.drop_table("spm_endpoint")
