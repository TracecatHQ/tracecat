"""Add event sources, triggers, trigger bindings and the event outbox.

Revision ID: d55f7e6e72a2
Revises: e573b3acd133
Create Date: 2026-11-27 12:00:00.000000

Additive: five new tables, nothing existing changes. ``trigger`` is a generic
header; each trigger kind with settings has its own detail table, here
``object_storage_trigger``. ``event_source`` is generic over the source kind
(``aws_sns`` so far). The downgrade drops the tables with their rows,
including any queued events.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import SchemaItem

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

revision: str = "d55f7e6e72a2"
down_revision: str | None = "e573b3acd133"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Parents first, so downgrade can walk the tuple in reverse.
_TABLES = (
    "event_source",
    "trigger",
    "object_storage_trigger",
    "trigger_binding",
    "event_outbox",
)


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.TIMESTAMP(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _workspace_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspace.id"],
        name=op.f(f"fk_{table}_workspace_id_workspace"),
        ondelete="CASCADE",
    )


def _same_workspace_fk(
    table: str, column: str, target: str, ondelete: str | None = "CASCADE"
) -> sa.ForeignKeyConstraint:
    """A foreign key that also requires the target to be in the row's workspace."""
    return sa.ForeignKeyConstraint(
        [column, "workspace_id"],
        [f"{target}.id", f"{target}.workspace_id"],
        name=op.f(f"fk_{table}_{column}_workspace_id_{target}"),
        ondelete=ondelete,
    )


def _same_workspace_key(table: str) -> sa.UniqueConstraint:
    """The target of children's same-workspace foreign keys."""
    return sa.UniqueConstraint(
        "id", "workspace_id", name=op.f(f"uq_{table}_id_workspace_id")
    )


def _create_config_table(table: str, *items: SchemaItem) -> None:
    """Create a workspace-owned config table with RLS."""
    op.create_table(
        table,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _workspace_fk(table),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f(f"pk_{table}")),
        *items,
    )
    op.create_index(op.f(f"ix_{table}_id"), table, ["id"], unique=True)
    op.execute(enable_workspace_table_rls(table))


def upgrade() -> None:
    _create_config_table(
        "event_source",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("subscription_status", sa.String(length=16), nullable=False),
        sa.Column("subscription_ref", sa.String(length=512), nullable=True),
        sa.Column("endpoint_token", sa.String(length=64), nullable=False),
        _same_workspace_key("event_source"),
        sa.UniqueConstraint(
            "endpoint_token", name=op.f("uq_event_source_endpoint_token")
        ),
    )
    # One source per SNS topic per workspace.
    op.create_index(
        "uq_event_source_aws_sns_topic",
        "event_source",
        ["workspace_id", sa.text("(config ->> 'topic_arn')")],
        unique=True,
        postgresql_where=sa.text("kind = 'aws_sns'"),
    )

    _create_config_table(
        "trigger",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        _same_workspace_key("trigger"),
    )

    op.create_table(
        "object_storage_trigger",
        sa.Column("trigger_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column(
            "key_prefix", sa.String(length=1024), server_default="", nullable=False
        ),
        _workspace_fk("object_storage_trigger"),
        _same_workspace_fk("object_storage_trigger", "trigger_id", "trigger"),
        # NO ACTION, not RESTRICT: a workspace delete cascades to the source
        # and to this row in one statement, and NO ACTION checks at its end.
        _same_workspace_fk(
            "object_storage_trigger", "source_id", "event_source", ondelete=None
        ),
        sa.PrimaryKeyConstraint("trigger_id", name=op.f("pk_object_storage_trigger")),
    )
    op.create_index(
        "ix_object_storage_trigger_source_id", "object_storage_trigger", ["source_id"]
    )
    op.execute(enable_workspace_table_rls("object_storage_trigger"))

    _create_config_table(
        "trigger_binding",
        sa.Column("trigger_id", sa.UUID(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=True),
        sa.Column("agent_preset_id", sa.UUID(), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        _same_workspace_fk("trigger_binding", "trigger_id", "trigger"),
        _same_workspace_fk("trigger_binding", "agent_preset_id", "agent_preset"),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflow.id"],
            name=op.f("fk_trigger_binding_workflow_id_workflow"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(target_type = 'workflow' AND workflow_id IS NOT NULL"
            " AND agent_preset_id IS NULL AND instruction IS NULL)"
            " OR (target_type = 'agent' AND agent_preset_id IS NOT NULL"
            " AND workflow_id IS NULL AND instruction IS NOT NULL)",
            name=op.f("ck_trigger_binding_target"),
        ),
        sa.UniqueConstraint(
            "trigger_id",
            "workflow_id",
            name=op.f("uq_trigger_binding_trigger_id_workflow_id"),
        ),
        sa.UniqueConstraint(
            "trigger_id",
            "agent_preset_id",
            name=op.f("uq_trigger_binding_trigger_id_agent_preset_id"),
        ),
    )
    op.create_index(
        "ix_trigger_binding_workflow_id", "trigger_binding", ["workflow_id"]
    )
    op.create_index(
        "ix_trigger_binding_agent_preset_id", "trigger_binding", ["agent_preset_id"]
    )

    op.create_table(
        "event_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # NULL once a row stops retrying.
        sa.Column(
            "next_attempt_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _workspace_fk("event_outbox"),
        _same_workspace_fk("event_outbox", "source_id", "event_source"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_outbox")),
        sa.UniqueConstraint(
            "source_id",
            "occurrence_key",
            name=op.f("uq_event_outbox_source_id_occurrence_key"),
        ),
    )
    op.create_index(
        "ix_event_outbox_dispatch",
        "event_outbox",
        ["workspace_id", "next_attempt_at", "id"],
    )
    op.execute(enable_workspace_table_rls("event_outbox"))


def downgrade() -> None:
    # Dropping a table drops its indexes, constraints and policies with it.
    for table in reversed(_TABLES):
        op.execute(disable_workspace_table_rls(table))
        op.drop_table(table)
