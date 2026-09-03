"""Normalize agent preset subagent edges.

Revision ID: 7b4d9a2c1e6f
Revises: 44d7e75b6f4c
Create Date: 2026-09-02 17:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

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


def _backfill_bindings(
    *,
    parent_table: str,
    edge_table: str,
    parent_column: str,
) -> None:
    """Project the retained JSON binding into normalized edge rows.

    ID-backed refs are preferred. Slug-only refs prefer the active preset and
    otherwise resolve only when exactly one tombstone owns the slug. This keeps
    legitimate soft-deleted child references while rejecting ambiguous data.
    """

    temp_table = f"_{edge_table}_backfill"
    op.execute(
        sa.text(
            f"""
            CREATE TEMP TABLE {temp_table}
            ON COMMIT DROP AS
            WITH refs AS (
                SELECT
                    parent.id AS parent_id,
                    parent.workspace_id,
                    ref.value AS ref
                FROM {parent_table} AS parent
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(parent.agents -> 'subagents') = 'array'
                        THEN parent.agents -> 'subagents'
                        ELSE '[]'::jsonb
                    END
                ) AS ref(value)
            )
            SELECT
                refs.parent_id,
                refs.workspace_id,
                CASE
                    WHEN refs.ref ->> 'preset_id' IS NOT NULL THEN child_by_id.id
                    WHEN child_by_slug.active_match_count = 1
                        THEN child_by_slug.child_id
                    WHEN child_by_slug.active_match_count = 0
                        AND child_by_slug.total_match_count = 1
                        THEN child_by_slug.child_id
                    ELSE NULL
                END AS child_id,
                COALESCE(NULLIF(refs.ref ->> 'name', ''), refs.ref ->> 'preset')
                    AS alias,
                refs.ref ->> 'description' AS description,
                CASE
                    WHEN jsonb_typeof(refs.ref -> 'max_turns') = 'number'
                    THEN (refs.ref ->> 'max_turns')::integer
                    ELSE NULL
                END AS max_turns
            FROM refs
            LEFT JOIN agent_preset AS child_by_id
                ON child_by_id.workspace_id = refs.workspace_id
                AND child_by_id.id = CASE
                    WHEN refs.ref ->> 'preset_id' ~*
                        '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[1-5][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
                    THEN (refs.ref ->> 'preset_id')::uuid
                    ELSE NULL
                END
            LEFT JOIN LATERAL (
                SELECT
                    (
                        array_agg(
                            candidate.id
                            ORDER BY
                                (candidate.deleted_at IS NULL) DESC,
                                candidate.id
                        )
                    )[1] AS child_id,
                    count(*) FILTER (WHERE candidate.deleted_at IS NULL)
                        AS active_match_count,
                    count(*) AS total_match_count
                FROM agent_preset AS candidate
                WHERE candidate.workspace_id = refs.workspace_id
                    AND candidate.slug = refs.ref ->> 'preset'
            ) AS child_by_slug ON TRUE
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM {temp_table}
                    WHERE child_id IS NULL OR alias IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset subagents: unresolved or cross-workspace reference';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM {temp_table}
                    GROUP BY workspace_id, parent_id, alias
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot normalize agent preset subagents: duplicate alias';
                END IF;
            END $$;

            INSERT INTO {edge_table} (
                id,
                workspace_id,
                {parent_column},
                child_preset_id,
                alias,
                description,
                max_turns
            )
            SELECT
                gen_random_uuid(),
                workspace_id,
                parent_id,
                child_id,
                alias,
                description,
                max_turns
            FROM {temp_table}
            ORDER BY parent_id, alias;
            """
        )
    )


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
    _backfill_bindings(
        parent_table="agent_preset",
        edge_table="agent_preset_subagent",
        parent_column="parent_preset_id",
    )
    _backfill_bindings(
        parent_table="agent_preset_version",
        edge_table="agent_preset_version_subagent",
        parent_column="parent_preset_version_id",
    )


def downgrade() -> None:
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
