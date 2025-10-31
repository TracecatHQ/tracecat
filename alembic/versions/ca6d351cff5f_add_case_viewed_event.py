"""add case viewed event

Revision ID: ca6d351cff5f
Revises: 2d6eadcf4976
Create Date: 2025-10-31 14:13:14.498933

"""

from collections.abc import Sequence

from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "ca6d351cff5f"
down_revision: str | None = "2d6eadcf4976"
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
            "PAYLOAD_CHANGED",
            "TASK_CREATED",
            "TASK_DELETED",
            "TASK_STATUS_CHANGED",
            "TASK_PRIORITY_CHANGED",
            "TASK_WORKFLOW_CHANGED",
            "TASK_ASSIGNEE_CHANGED",
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
    op.sync_enum_values(  # type: ignore[attr-defined]
        enum_schema="public",
        enum_name="caseeventtype",
        new_values=[
            "CASE_CREATED",
            "CASE_UPDATED",
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
