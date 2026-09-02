"""Normalize agent preset subagent edges.

Revision ID: 7b4d9a2c1e6f
Revises: 44d7e75b6f4c
Create Date: 2026-09-02 17:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "7b4d9a2c1e6f"
down_revision: str | None = "44d7e75b6f4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _record_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), sa.Identity(), nullable=False),
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
    ]


def _create_edge_table(*, table: str, parent_column: str) -> None:
    parent_table = (
        "agent_preset_version"
        if parent_column == "parent_preset_version_id"
        else "agent_preset"
    )
    parent_constraint = (
        "fk_ap_version_subagent_workspace_parent_version"
        if parent_column == "parent_preset_version_id"
        else "fk_agent_preset_subagent_workspace_parent_agent_preset"
    )
    unique_name = (
        "uq_agent_preset_version_subagent_workspace_parent_alias"
        if parent_column == "parent_preset_version_id"
        else "uq_agent_preset_subagent_workspace_parent_alias"
    )
    check_name = f"ck_{table}_max_turns_positive"

    op.create_table(
        table,
        *_record_columns(),
        sa.Column(parent_column, sa.UUID(), nullable=False),
        sa.Column("child_preset_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "max_turns IS NULL OR max_turns >= 1",
            name=check_name,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", parent_column],
            [f"{parent_table}.workspace_id", f"{parent_table}.id"],
            name=parent_constraint,
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "child_preset_id"],
            ["agent_preset.workspace_id", "agent_preset.id"],
            name=(
                "fk_agent_preset_version_subagent_workspace_child_agent_preset"
                if parent_column == "parent_preset_version_id"
                else "fk_agent_preset_subagent_workspace_child_agent_preset"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f(f"fk_{table}_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f(f"pk_{table}")),
        sa.UniqueConstraint(
            "workspace_id",
            parent_column,
            "alias",
            name=unique_name,
        ),
    )
    op.create_index(op.f(f"ix_{table}_id"), table, ["id"], unique=True)
    op.create_index(
        op.f(f"ix_{table}_{parent_column}"),
        table,
        [parent_column],
        unique=False,
    )
    op.create_index(
        op.f(f"ix_{table}_child_preset_id"),
        table,
        ["child_preset_id"],
        unique=False,
    )
    op.execute(enable_workspace_table_rls(table))


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_agent_preset_workspace_id_id",
        "agent_preset",
        ["workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_agent_preset_version_workspace_id_id",
        "agent_preset_version",
        ["workspace_id", "id"],
    )
    _create_edge_table(
        table="agent_preset_subagent",
        parent_column="parent_preset_id",
    )
    _create_edge_table(
        table="agent_preset_version_subagent",
        parent_column="parent_preset_version_id",
    )
    op.drop_column("agent_preset_version", "agents")
    op.drop_column("agent_preset", "agents")


def _restore_agents_json(
    *,
    parent_table: str,
    edge_table: str,
    parent_column: str,
) -> None:
    """Materialize normalized edges into the preceding JSONB representation."""

    op.execute(
        sa.text(
            f"""
            UPDATE {parent_table} AS parent
            SET agents = jsonb_build_object(
                'subagents',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            jsonb_build_object(
                                'preset', child.slug,
                                'preset_id', child.id,
                                'preset_version_id', child.current_version_id,
                                'preset_version', child_version.version,
                                'name', edge.alias,
                                'description', edge.description,
                                'max_turns', edge.max_turns
                            )
                            ORDER BY edge.alias
                        )
                        FROM {edge_table} AS edge
                        JOIN agent_preset AS child
                          ON child.workspace_id = edge.workspace_id
                         AND child.id = edge.child_preset_id
                        JOIN agent_preset_version AS child_version
                          ON child_version.workspace_id = child.workspace_id
                         AND child_version.id = child.current_version_id
                        WHERE edge.workspace_id = parent.workspace_id
                          AND edge.{parent_column} = parent.id
                    ),
                    '[]'::jsonb
                )
            )
            """
        )
    )


def downgrade() -> None:
    empty_agents = sa.text("'{\"subagents\": []}'::jsonb")
    op.add_column(
        "agent_preset",
        sa.Column(
            "agents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=empty_agents,
            nullable=False,
        ),
    )
    op.add_column(
        "agent_preset_version",
        sa.Column(
            "agents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=empty_agents,
            nullable=False,
        ),
    )
    _restore_agents_json(
        parent_table="agent_preset",
        edge_table="agent_preset_subagent",
        parent_column="parent_preset_id",
    )
    _restore_agents_json(
        parent_table="agent_preset_version",
        edge_table="agent_preset_version_subagent",
        parent_column="parent_preset_version_id",
    )
    for table in (
        "agent_preset_version_subagent",
        "agent_preset_subagent",
    ):
        op.execute(disable_workspace_table_rls(table))
        op.drop_table(table)
    op.drop_constraint(
        "uq_agent_preset_version_workspace_id_id",
        "agent_preset_version",
        type_="unique",
    )
    op.drop_constraint(
        "uq_agent_preset_workspace_id_id",
        "agent_preset",
        type_="unique",
    )
