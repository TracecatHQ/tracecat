"""Add case viewed event type

Revision ID: 5d59cb88f9a7
Revises: 2d6eadcf4976
Create Date: 2025-10-31 00:25:00.000000

"""

from collections.abc import Sequence

from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "5d59cb88f9a7"
down_revision: str | None = "2d6eadcf4976"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CASE_EVENT_ENUM_VALUES = [
    "CASE_CREATED",
    "CASE_UPDATED",
    "CASE_VIEWED",
    "CASE_CLOSED",
    "CASE_REOPENED",
    "PRIORITY_CHANGED",
    "SEVERITY_CHANGED",
    "STATUS_CHANGED",
    "FIELDS_CHANGED",
    "ASSIGNEE_CHANGED",
    "ATTACHMENT_CREATED",
    "ATTACHMENT_DELETED",
    "PAYLOAD_CHANGED",
    "TASK_CREATED",
    "TASK_DELETED",
    "TASK_STATUS_CHANGED",
    "TASK_PRIORITY_CHANGED",
    "TASK_WORKFLOW_CHANGED",
    "TASK_ASSIGNEE_CHANGED",
]


def upgrade() -> None:
    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=CASE_EVENT_ENUM_VALUES,
        affected_columns=[
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
        ],
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    values_without_view = [
        value for value in CASE_EVENT_ENUM_VALUES if value != "CASE_VIEWED"
    ]
    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=values_without_view,
        affected_columns=[
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
        ],
        enum_values_to_rename=[],
    )
