"""add action graph fields

Revision ID: 9079ae19374b
Revises: 0fd1f09cd98b
Create Date: 2025-12-03 15:22:20.734638

"""

import json
import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9079ae19374b"
down_revision: str | None = "4c6310af479d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_ACTION_ID_PATTERN = re.compile(r"^act-([0-9a-fA-F]{32})$")


def _coerce_action_uuid(value: object) -> uuid.UUID | None:
    """Coerce action IDs from legacy/current formats into UUID."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value

    value_str = str(value)
    if match := _LEGACY_ACTION_ID_PATTERN.fullmatch(value_str):
        try:
            return uuid.UUID(hex=match.group(1))
        except ValueError:
            return None

    try:
        return uuid.UUID(value_str)
    except (ValueError, AttributeError, TypeError):
        return None


def _normalize_source_id(value: object) -> str:
    """Normalize source_id to trigger-* or UUID string."""
    source_id = str(value) if value is not None else ""
    if source_id.startswith("trigger-"):
        return source_id
    if source_uuid := _coerce_action_uuid(source_id):
        return str(source_uuid)
    return source_id


def upgrade() -> None:
    # 1. Add new columns with server defaults
    op.add_column(
        "action",
        sa.Column(
            "position_x", sa.Float(), nullable=False, server_default=sa.text("0.0")
        ),
    )
    op.add_column(
        "action",
        sa.Column(
            "position_y", sa.Float(), nullable=False, server_default=sa.text("0.0")
        ),
    )
    op.add_column(
        "action",
        sa.Column(
            "upstream_edges",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "workflow",
        sa.Column(
            "trigger_position_x",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.add_column(
        "workflow",
        sa.Column(
            "trigger_position_y",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.add_column(
        "workflow",
        sa.Column(
            "graph_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "workflow",
        sa.Column(
            "viewport_x",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.add_column(
        "workflow",
        sa.Column(
            "viewport_y",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.add_column(
        "workflow",
        sa.Column(
            "viewport_zoom",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
    )

    # 2. Migrate data from workflow.object to new fields
    conn = op.get_bind()
    workflows = conn.execute(
        sa.text("SELECT id, object FROM workflow WHERE object IS NOT NULL")
    ).fetchall()

    for wf_id, obj in workflows:
        if not obj:
            continue

        # Parse nodes and edges from workflow.object
        nodes = obj.get("nodes", [])
        edges = obj.get("edges", [])

        # Update trigger position and viewport
        viewport = obj.get("viewport", {})
        for node in nodes:
            if node.get("type") == "trigger":
                pos = node.get("position", {})
                conn.execute(
                    sa.text("""
                        UPDATE workflow
                        SET trigger_position_x = :tx, trigger_position_y = :ty,
                            viewport_x = :vx, viewport_y = :vy, viewport_zoom = :vz
                        WHERE id = :id
                    """),
                    {
                        "tx": pos.get("x", 0),
                        "ty": pos.get("y", 0),
                        "vx": viewport.get("x", 0),
                        "vy": viewport.get("y", 0),
                        "vz": viewport.get("zoom", 1),
                        "id": wf_id,
                    },
                )

        # Update action positions and upstream_edges
        for node in nodes:
            if node.get("type") != "udf":
                continue
            action_id = _coerce_action_uuid(node.get("id"))
            if action_id is None:
                continue
            pos = node.get("position", {})

            # Find all incoming edges for this action (including trigger edges)
            incoming = [
                e for e in edges if _coerce_action_uuid(e.get("target")) == action_id
            ]
            upstream = []
            for e in incoming:
                source_id = _normalize_source_id(e.get("source", ""))
                is_trigger = source_id.startswith("trigger-")

                # Support both camelCase and snake_case edge payloads.
                source_handle = e.get("sourceHandle") or e.get("source_handle")
                if source_handle not in ("success", "error"):
                    source_handle = "success"

                edge_data: dict[str, str] = {
                    "source_id": source_id,
                    "source_type": "trigger" if is_trigger else "udf",
                }
                # Only add source_handle for udf edges
                if not is_trigger:
                    edge_data["source_handle"] = source_handle
                upstream.append(edge_data)

            conn.execute(
                sa.text("""
                    UPDATE action
                    SET position_x = :x, position_y = :y, upstream_edges = :edges
                    WHERE id = :id
                """),
                {
                    "x": pos.get("x", 0),
                    "y": pos.get("y", 0),
                    "edges": json.dumps(upstream),
                    "id": str(action_id),
                },
            )

    # 3. Drop the legacy object column (data has been migrated to normalized fields)
    op.drop_column("workflow", "object")


def downgrade() -> None:
    # 1. Re-add the object column first (needed for data migration)
    op.add_column(
        "workflow",
        sa.Column("object", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 2. Migrate data back to workflow.object from new fields
    conn = op.get_bind()

    # Get all workflows with their graph data
    workflows = conn.execute(
        sa.text("""
            SELECT id, object, trigger_position_x, trigger_position_y,
                   viewport_x, viewport_y, viewport_zoom
            FROM workflow
        """)
    ).fetchall()

    for row in workflows:
        wf_id = row[0]
        obj = row[1] or {}
        trigger_x, trigger_y = row[2], row[3]
        viewport_x, viewport_y, viewport_zoom = row[4], row[5], row[6]

        # Get all actions for this workflow
        actions = conn.execute(
            sa.text("""
                SELECT id, title, position_x, position_y, upstream_edges
                FROM action
                WHERE workflow_id = :wf_id
            """),
            {"wf_id": wf_id},
        ).fetchall()

        # Reconstruct nodes
        nodes = []

        # Add trigger node - convert workflow UUID to string for JSON
        trigger_id = f"trigger-{wf_id}"
        nodes.append(
            {
                "id": trigger_id,
                "type": "trigger",
                "position": {"x": trigger_x, "y": trigger_y},
            }
        )

        # Add action nodes
        for action in actions:
            action_id, title, pos_x, pos_y, upstream_edges = action
            # Convert UUID to string for JSON serialization
            action_id_str = str(action_id)
            nodes.append(
                {
                    "id": action_id_str,
                    "type": "udf",
                    "position": {"x": pos_x, "y": pos_y},
                    "data": {"title": title},
                }
            )

        # Reconstruct edges from upstream_edges
        edges = []
        for action in actions:
            action_id = action[0]
            # Convert UUID to string for JSON serialization
            action_id_str = str(action_id)
            upstream_edges = action[4] or []
            for edge in upstream_edges:
                source_id = edge.get("source_id", "")
                source_type = edge.get("source_type", "udf")
                edge_data = {
                    "source": source_id,
                    "target": action_id_str,
                }
                if source_type != "trigger":
                    edge_data["sourceHandle"] = edge.get("source_handle", "success")
                edges.append(edge_data)

        # Update workflow.object
        obj["nodes"] = nodes
        obj["edges"] = edges
        obj["viewport"] = {"x": viewport_x, "y": viewport_y, "zoom": viewport_zoom}

        conn.execute(
            sa.text("UPDATE workflow SET object = :obj WHERE id = :id"),
            {"obj": json.dumps(obj), "id": wf_id},
        )

    # 3. Drop the new columns
    op.drop_column("workflow", "viewport_zoom")
    op.drop_column("workflow", "viewport_y")
    op.drop_column("workflow", "viewport_x")
    op.drop_column("workflow", "graph_version")
    op.drop_column("workflow", "trigger_position_y")
    op.drop_column("workflow", "trigger_position_x")
    op.drop_column("action", "upstream_edges")
    op.drop_column("action", "position_y")
    op.drop_column("action", "position_x")
