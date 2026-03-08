"""add comment activity and audit events

Revision ID: 3b58a1430e95
Revises: b42892363e72
Create Date: 2026-03-08 01:38:54.400510

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic_postgresql_enum import TableReference
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b58a1430e95"
down_revision: str | None = "b42892363e72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT_EVENT_TYPES = [
    "COMMENT_CREATED",
    "COMMENT_UPDATED",
    "COMMENT_DELETED",
    "COMMENT_REPLY_CREATED",
    "COMMENT_REPLY_UPDATED",
    "COMMENT_REPLY_DELETED",
]

_CASE_EVENT_TYPE_VALUES = [
    "CASE_CREATED",
    "CASE_UPDATED",
    "CASE_CLOSED",
    "CASE_REOPENED",
    "CASE_VIEWED",
    "PRIORITY_CHANGED",
    "SEVERITY_CHANGED",
    "STATUS_CHANGED",
    "FIELDS_CHANGED",
    "ASSIGNEE_CHANGED",
    "ATTACHMENT_CREATED",
    "ATTACHMENT_DELETED",
    "TAG_ADDED",
    "TAG_REMOVED",
    "PAYLOAD_CHANGED",
    "TASK_CREATED",
    "TASK_DELETED",
    "TASK_STATUS_CHANGED",
    "TASK_PRIORITY_CHANGED",
    "TASK_WORKFLOW_CHANGED",
    "TASK_ASSIGNEE_CHANGED",
    "DROPDOWN_VALUE_CHANGED",
    "TABLE_ROW_LINKED",
    "TABLE_ROW_UNLINKED",
    *_COMMENT_EVENT_TYPES,
]

_PREVIOUS_CASE_EVENT_TYPE_VALUES = [
    value for value in _CASE_EVENT_TYPE_VALUES if value not in _COMMENT_EVENT_TYPES
]

_CASE_EVENT_TYPE_COLUMNS = [
    TableReference(
        table_schema="public",
        table_name="case_duration_definition",
        column_name="end_event_type",
    ),
    TableReference(
        table_schema="public",
        table_name="case_duration_definition",
        column_name="start_event_type",
    ),
    TableReference(
        table_schema="public",
        table_name="case_event",
        column_name="type",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("audit_event"):
        op.create_table(
            "audit_event",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("organization_id", sa.UUID(), nullable=True),
            sa.Column("workspace_id", sa.UUID(), nullable=True),
            sa.Column("actor_type", sa.String(length=32), nullable=False),
            sa.Column("actor_id", sa.UUID(), nullable=False),
            sa.Column("actor_label", sa.String(length=255), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
            sa.Column("resource_type", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.UUID(), nullable=True),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column(
                "data",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
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
            sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        )
        existing_indexes: set[str] = set()
    else:
        existing_indexes = {
            name
            for index in inspector.get_indexes("audit_event")
            if (name := index["name"]) is not None
        }

    if "ix_audit_event_created_at" not in existing_indexes:
        op.create_index(
            "ix_audit_event_created_at",
            "audit_event",
            ["created_at"],
            unique=False,
        )
    if "ix_audit_event_organization_id_created_at" not in existing_indexes:
        op.create_index(
            "ix_audit_event_organization_id_created_at",
            "audit_event",
            ["organization_id", "created_at"],
            unique=False,
        )
    if "ix_audit_event_workspace_id_created_at" not in existing_indexes:
        op.create_index(
            "ix_audit_event_workspace_id_created_at",
            "audit_event",
            ["workspace_id", "created_at"],
            unique=False,
        )
    if "ix_audit_event_resource_type_resource_id_created_at" not in existing_indexes:
        op.create_index(
            "ix_audit_event_resource_type_resource_id_created_at",
            "audit_event",
            ["resource_type", "resource_id", "created_at"],
            unique=False,
        )
    if "ix_audit_event_actor_id_created_at" not in existing_indexes:
        op.create_index(
            "ix_audit_event_actor_id_created_at",
            "audit_event",
            ["actor_id", "created_at"],
            unique=False,
        )

    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=_CASE_EVENT_TYPE_VALUES,
        affected_columns=_CASE_EVENT_TYPE_COLUMNS,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM case_duration_definition
            WHERE start_event_type IN :comment_event_types
               OR end_event_type IN :comment_event_types
            """
        ).bindparams(sa.bindparam("comment_event_types", expanding=True)),
        {"comment_event_types": _COMMENT_EVENT_TYPES},
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM case_event
            WHERE type IN :comment_event_types
            """
        ).bindparams(sa.bindparam("comment_event_types", expanding=True)),
        {"comment_event_types": _COMMENT_EVENT_TYPES},
    )

    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=_PREVIOUS_CASE_EVENT_TYPE_VALUES,
        affected_columns=_CASE_EVENT_TYPE_COLUMNS,
        enum_values_to_rename=[],
    )

    op.drop_index("ix_audit_event_actor_id_created_at", table_name="audit_event")
    op.drop_index(
        "ix_audit_event_resource_type_resource_id_created_at",
        table_name="audit_event",
    )
    op.drop_index("ix_audit_event_workspace_id_created_at", table_name="audit_event")
    op.drop_index(
        "ix_audit_event_organization_id_created_at",
        table_name="audit_event",
    )
    op.drop_index("ix_audit_event_created_at", table_name="audit_event")
    op.drop_table("audit_event")
