"""add comment activity events

Revision ID: 3b58a1430e95
Revises: b42892363e72
Create Date: 2026-03-08 01:38:54.400510

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic_postgresql_enum import TableReference

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
        {"comment_event_types": tuple(_COMMENT_EVENT_TYPES)},
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM case_event
            WHERE type IN :comment_event_types
            """
        ).bindparams(sa.bindparam("comment_event_types", expanding=True)),
        {"comment_event_types": tuple(_COMMENT_EVENT_TYPES)},
    )

    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=_PREVIOUS_CASE_EVENT_TYPE_VALUES,
        affected_columns=_CASE_EVENT_TYPE_COLUMNS,
        enum_values_to_rename=[],
    )
