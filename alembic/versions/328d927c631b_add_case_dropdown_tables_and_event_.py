"""add case dropdown tables and event type

Revision ID: 328d927c631b
Revises: 5a3b7c8d9e0f
Create Date: 2026-01-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic_postgresql_enum import TableReference

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "328d927c631b"
down_revision: str | None = "5a3b7c8d9e0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add DROPDOWN_VALUE_CHANGED to caseeventtype enum
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

    # Case Dropdown Definition
    op.create_table(
        "case_dropdown_definition",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column(
            "is_ordered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("icon_name", sa.String(length=100), nullable=True),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name="fk_case_dropdown_definition_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint(
            "ref", "workspace_id", name="uq_case_dropdown_definition_ref_workspace"
        ),
    )
    op.create_index(
        "ix_case_dropdown_definition_id",
        "case_dropdown_definition",
        ["id"],
    )
    op.create_index(
        "ix_case_dropdown_definition_ref",
        "case_dropdown_definition",
        ["ref"],
    )

    # Case Dropdown Option
    op.create_table(
        "case_dropdown_option",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("definition_id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("icon_name", sa.String(length=100), nullable=True),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column(
            "position", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["case_dropdown_definition.id"],
            name="fk_case_dropdown_option_definition",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint(
            "ref", "definition_id", name="uq_case_dropdown_option_ref_definition"
        ),
    )
    op.create_index(
        "ix_case_dropdown_option_id",
        "case_dropdown_option",
        ["id"],
    )
    op.create_index(
        "ix_case_dropdown_option_ref",
        "case_dropdown_option",
        ["ref"],
    )

    # Case Dropdown Value (per-case single-select)
    op.create_table(
        "case_dropdown_value",
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("definition_id", sa.UUID(), nullable=False),
        sa.Column("option_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["case.id"],
            name="fk_case_dropdown_value_case",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["case_dropdown_definition.id"],
            name="fk_case_dropdown_value_definition",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["option_id"],
            ["case_dropdown_option.id"],
            name="fk_case_dropdown_value_option",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "definition_id",
            name="uq_case_dropdown_value_case_definition",
        ),
    )
    op.create_index(
        "ix_case_dropdown_value_id",
        "case_dropdown_value",
        ["id"],
    )


def downgrade() -> None:
    # Drop tables in reverse FK order
    op.drop_table("case_dropdown_value")
    op.drop_table("case_dropdown_option")
    op.drop_table("case_dropdown_definition")

    # Delete any case_event rows referencing the enum value we're about to remove
    op.execute(
        "DELETE FROM case_event WHERE type = 'DROPDOWN_VALUE_CHANGED'"
    )
    # Also clean up case_duration_definition rows if any reference the removed value
    op.execute(
        "DELETE FROM case_duration_definition"
        " WHERE start_event_type = 'DROPDOWN_VALUE_CHANGED'"
        " OR end_event_type = 'DROPDOWN_VALUE_CHANGED'"
    )

    # Revert caseeventtype enum to 21 values (without DROPDOWN_VALUE_CHANGED)
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
