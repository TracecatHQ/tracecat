"""add case table row event types

Revision ID: 8f4f1bd13e9c
Revises: 1224a945b9f7
Create Date: 2026-03-01 10:02:00.000000

"""

from collections.abc import Sequence

from alembic_postgresql_enum import TableReference

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f4f1bd13e9c"
down_revision: str | None = "1224a945b9f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=[
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
        ],
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
    op.execute(
        "DELETE FROM case_duration_definition "
        "WHERE start_event_type IN ('TABLE_ROW_LINKED', 'TABLE_ROW_UNLINKED') "
        "OR end_event_type IN ('TABLE_ROW_LINKED', 'TABLE_ROW_UNLINKED')"
    )
    op.execute(
        "DELETE FROM case_event WHERE type IN ('TABLE_ROW_LINKED', 'TABLE_ROW_UNLINKED')"
    )

    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=[
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
        ],
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
