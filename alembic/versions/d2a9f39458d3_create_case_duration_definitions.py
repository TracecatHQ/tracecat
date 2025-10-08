"""create case duration definitions table

Revision ID: d2a9f39458d3
Revises: c2a4f8a5cf72
Create Date: 2025-10-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2a9f39458d3"
down_revision: str | None = "c2a4f8a5cf72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    casedurationanchorselection = sa.Enum(
        "first",
        "last",
        name="casedurationanchorselection",
    )
    casedurationanchorselection.create(op.get_bind())

    op.create_table(
        "case_duration_definition",
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
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column(
            "start_event_type",
            postgresql.ENUM(
                "CASE_CREATED",
                "CASE_UPDATED",
                "CASE_CLOSED",
                "CASE_REOPENED",
                "PRIORITY_CHANGED",
                "SEVERITY_CHANGED",
                "STATUS_CHANGED",
                "FIELDS_CHANGED",
                "ASSIGNEE_CHANGED",
                name="caseeventtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "start_timestamp_path",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("'created_at'"),
        ),
        sa.Column(
            "start_field_filters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "start_selection",
            postgresql.ENUM(
                "first",
                "last",
                name="casedurationanchorselection",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'first'"),
        ),
        sa.Column(
            "end_event_type",
            postgresql.ENUM(
                "CASE_CREATED",
                "CASE_UPDATED",
                "CASE_CLOSED",
                "CASE_REOPENED",
                "PRIORITY_CHANGED",
                "SEVERITY_CHANGED",
                "STATUS_CHANGED",
                "FIELDS_CHANGED",
                "ASSIGNEE_CHANGED",
                name="caseeventtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "end_timestamp_path",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("'created_at'"),
        ),
        sa.Column(
            "end_field_filters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "end_selection",
            postgresql.ENUM(
                "first",
                "last",
                name="casedurationanchorselection",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'first'"),
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("surrogate_id"),
        sa.UniqueConstraint(
            "owner_id",
            "name",
            name="uq_case_duration_definition_owner_name",
        ),
    )
    op.create_index(
        op.f("ix_case_duration_definition_id"),
        "case_duration_definition",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_case_duration_definition_name"),
        "case_duration_definition",
        ["name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_case_duration_definition_name"),
        table_name="case_duration_definition",
    )
    op.drop_index(
        op.f("ix_case_duration_definition_id"),
        table_name="case_duration_definition",
    )
    op.drop_table("case_duration_definition")
    sa.Enum(name="casedurationanchorselection").drop(op.get_bind())
