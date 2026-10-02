"""add reference projections and turn snapshots

Revision ID: fbc6e4b5bbf2
Revises: 6d83f2a91c40
Create Date: 2026-10-02 12:14:21.400850

Expand only: nullable markers preserve old writes; no source/blob backfill.
Downgrade removes only these new structures. Stop all reference writers before
downgrading; application rollback alone must retain this schema and run objects.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fbc6e4b5bbf2"
down_revision: str | None = "6d83f2a91c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_preset_version",
        sa.Column("reference_schema_version", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_agent_preset_version_workspace_id",
        "agent_preset_version",
        ["workspace_id", "id"],
    )
    op.create_unique_constraint(
        "uq_agent_session_workspace_id", "agent_session", ["workspace_id", "id"]
    )
    op.add_column(
        "skill_version",
        sa.Column("reference_schema_version", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_skill_version_workspace_id", "skill_version", ["workspace_id", "id"]
    )
    op.create_check_constraint(
        "ck_agent_preset_reference_schema_version",
        "agent_preset_version",
        "reference_schema_version IS NULL OR reference_schema_version = 1",
    )
    op.create_check_constraint(
        "ck_skill_reference_schema_version",
        "skill_version",
        "reference_schema_version IS NULL OR reference_schema_version = 1",
    )

    op.create_table(
        "agent_preset_version_reference",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("preset_version_id", sa.UUID(), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_key", sa.String(length=512), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("action_key", sa.String(length=255), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column(
            "occurrences", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "(kind = 'tool' AND action_key IS NOT NULL AND target_id IS NULL AND tool_name IS NULL) OR (kind = 'mcp-tool' AND target_id IS NOT NULL AND action_key IS NULL AND tool_name IS NOT NULL) OR (kind IN ('mcp-server', 'table', 'workflow', 'skill', 'agent') AND target_id IS NOT NULL AND action_key IS NULL AND tool_name IS NULL)",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_identity"),
        ),
        sa.CheckConstraint(
            "action_key IS NULL OR (action_key ~ '^[a-z0-9_]+([.][a-z0-9_]+)+$' AND action_key NOT LIKE 'mcp.%')",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_action"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(occurrences) = 'array' AND jsonb_array_length(occurrences) > 0",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_occurrences"),
        ),
        sa.CheckConstraint(
            "kind IN ('tool', 'mcp-server', 'mcp-tool', 'table', 'workflow', 'skill', 'agent')",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_kind"),
        ),
        sa.CheckConstraint(
            "source_path <> '' AND source_path NOT LIKE '/%' AND source_path !~ '(^|/)[.]{1,2}(/|$)' AND position('//' in source_path) = 0 AND right(source_path, 1) <> '/' AND position(chr(92) in source_path) = 0 AND source_path !~ '[[:cntrl:]]'",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_path"),
        ),
        sa.CheckConstraint(
            "source_path = 'instructions.md'",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_instructions"),
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_hash"),
        ),
        sa.CheckConstraint(
            "target_key = 'tracecat-ref://v1/' || kind || '/' || CASE WHEN kind = 'tool' THEN action_key WHEN kind = 'mcp-tool' THEN target_id::text || '/' || tool_name ELSE target_id::text END",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_target_key"),
        ),
        sa.CheckConstraint(
            "tool_name IS NULL OR tool_name ~ '^[A-Za-z0-9_-]+$'",
            name=op.f("ck_agent_preset_version_reference_ck_preset_ref_tool_name"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "preset_version_id"],
            ["agent_preset_version.workspace_id", "agent_preset_version.id"],
            name=op.f(
                "fk_agent_preset_version_reference_workspace_id_preset_version_id_agent_preset_version"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_preset_version_reference_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_agent_preset_version_reference")
        ),
        sa.UniqueConstraint("id", name=op.f("uq_agent_preset_version_reference_id")),
        sa.UniqueConstraint(
            "preset_version_id",
            "source_path",
            "target_key",
            name="uq_preset_ref_source_target",
        ),
    )
    op.create_index(
        "ix_preset_ref_reverse",
        "agent_preset_version_reference",
        ["workspace_id", "kind", "target_id"],
        unique=False,
    )
    op.create_index(
        "ix_preset_ref_source",
        "agent_preset_version_reference",
        ["workspace_id", "preset_version_id"],
        unique=False,
    )
    op.create_table(
        "skill_version_reference",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_version_id", sa.UUID(), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_key", sa.String(length=512), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("action_key", sa.String(length=255), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column(
            "occurrences", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "(kind = 'tool' AND action_key IS NOT NULL AND target_id IS NULL AND tool_name IS NULL) OR (kind = 'mcp-tool' AND target_id IS NOT NULL AND action_key IS NULL AND tool_name IS NOT NULL) OR (kind IN ('mcp-server', 'table', 'workflow', 'skill', 'agent') AND target_id IS NOT NULL AND action_key IS NULL AND tool_name IS NULL)",
            name=op.f("ck_skill_version_reference_ck_skill_ref_identity"),
        ),
        sa.CheckConstraint(
            "action_key IS NULL OR (action_key ~ '^[a-z0-9_]+([.][a-z0-9_]+)+$' AND action_key NOT LIKE 'mcp.%')",
            name=op.f("ck_skill_version_reference_ck_skill_ref_action"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(occurrences) = 'array' AND jsonb_array_length(occurrences) > 0",
            name=op.f("ck_skill_version_reference_ck_skill_ref_occurrences"),
        ),
        sa.CheckConstraint(
            "kind IN ('tool', 'mcp-server', 'mcp-tool', 'table', 'workflow', 'skill', 'agent')",
            name=op.f("ck_skill_version_reference_ck_skill_ref_kind"),
        ),
        sa.CheckConstraint(
            "source_path <> '' AND source_path NOT LIKE '/%' AND source_path !~ '(^|/)[.]{1,2}(/|$)' AND position('//' in source_path) = 0 AND right(source_path, 1) <> '/' AND position(chr(92) in source_path) = 0 AND source_path !~ '[[:cntrl:]]'",
            name=op.f("ck_skill_version_reference_ck_skill_ref_path"),
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_skill_version_reference_ck_skill_ref_hash"),
        ),
        sa.CheckConstraint(
            "target_key = 'tracecat-ref://v1/' || kind || '/' || CASE WHEN kind = 'tool' THEN action_key WHEN kind = 'mcp-tool' THEN target_id::text || '/' || tool_name ELSE target_id::text END",
            name=op.f("ck_skill_version_reference_ck_skill_ref_target_key"),
        ),
        sa.CheckConstraint(
            "tool_name IS NULL OR tool_name ~ '^[A-Za-z0-9_-]+$'",
            name=op.f("ck_skill_version_reference_ck_skill_ref_tool_name"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "skill_version_id"],
            ["skill_version.workspace_id", "skill_version.id"],
            name=op.f(
                "fk_skill_version_reference_workspace_id_skill_version_id_skill_version"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_skill_version_reference_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_skill_version_reference")
        ),
        sa.UniqueConstraint("id", name=op.f("uq_skill_version_reference_id")),
        sa.UniqueConstraint(
            "skill_version_id",
            "source_path",
            "target_key",
            name="uq_skill_ref_source_target",
        ),
    )
    op.create_index(
        "ix_skill_ref_reverse",
        "skill_version_reference",
        ["workspace_id", "kind", "target_id"],
        unique=False,
    )
    op.create_index(
        "ix_skill_ref_source",
        "skill_version_reference",
        ["workspace_id", "skill_version_id"],
        unique=False,
    )
    op.create_table(
        "agent_reference_run_snapshot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("logical_turn_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("backend_id", sa.String(length=50), nullable=False),
        sa.Column("harness_type", sa.String(length=50), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("graph_object_key", sa.Text(), nullable=False),
        sa.Column("graph_sha256", sa.String(length=64), nullable=False),
        sa.Column("final_object_key", sa.Text(), nullable=True),
        sa.Column("final_sha256", sa.String(length=64), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "(state = 'resolved' AND final_object_key IS NULL AND final_sha256 IS NULL) OR (state = 'ready' AND final_object_key IS NOT NULL AND final_sha256 IS NOT NULL)",
            name=op.f(
                "ck_agent_reference_run_snapshot_ck_reference_snapshot_readiness"
            ),
        ),
        sa.CheckConstraint(
            "backend_id <> '' AND harness_type <> '' AND graph_object_key <> '' AND (final_object_key IS NULL OR final_object_key <> '')",
            name=op.f("ck_agent_reference_run_snapshot_ck_reference_snapshot_keys"),
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[a-f0-9]{64}$' AND graph_sha256 ~ '^[a-f0-9]{64}$' AND (final_sha256 IS NULL OR final_sha256 ~ '^[a-f0-9]{64}$')",
            name=op.f("ck_agent_reference_run_snapshot_ck_reference_snapshot_hashes"),
        ),
        sa.CheckConstraint(
            "state IN ('resolved', 'ready')",
            name=op.f("ck_agent_reference_run_snapshot_ck_reference_snapshot_state"),
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name=op.f("ck_agent_reference_run_snapshot_ck_reference_snapshot_version"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "session_id"],
            ["agent_session.workspace_id", "agent_session.id"],
            name=op.f(
                "fk_agent_reference_run_snapshot_workspace_id_session_id_agent_session"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_reference_run_snapshot_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "surrogate_id", name=op.f("pk_agent_reference_run_snapshot")
        ),
        sa.UniqueConstraint("id", name=op.f("uq_agent_reference_run_snapshot_id")),
        sa.UniqueConstraint(
            "workspace_id", "logical_turn_id", name="uq_reference_snapshot_turn"
        ),
    )
    op.create_index(
        "ix_reference_snapshot_session",
        "agent_reference_run_snapshot",
        ["workspace_id", "session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reference_snapshot_session", table_name="agent_reference_run_snapshot"
    )
    op.drop_table("agent_reference_run_snapshot")
    op.drop_index("ix_skill_ref_source", table_name="skill_version_reference")
    op.drop_index("ix_skill_ref_reverse", table_name="skill_version_reference")
    op.drop_table("skill_version_reference")
    op.drop_index("ix_preset_ref_source", table_name="agent_preset_version_reference")
    op.drop_index("ix_preset_ref_reverse", table_name="agent_preset_version_reference")
    op.drop_table("agent_preset_version_reference")
    op.drop_constraint(
        "ck_skill_reference_schema_version", "skill_version", type_="check"
    )
    op.drop_constraint(
        "ck_agent_preset_reference_schema_version",
        "agent_preset_version",
        type_="check",
    )
    op.drop_constraint("uq_skill_version_workspace_id", "skill_version", type_="unique")
    op.drop_column("skill_version", "reference_schema_version")
    op.drop_constraint("uq_agent_session_workspace_id", "agent_session", type_="unique")
    op.drop_constraint(
        "uq_agent_preset_version_workspace_id", "agent_preset_version", type_="unique"
    )
    op.drop_column("agent_preset_version", "reference_schema_version")
