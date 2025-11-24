"""rename_search_records_actions

Revision ID: 7f4c4a2130b8
Revises: c2a4f8a5cf72
Create Date: 2025-01-20 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f4c4a2130b8"
down_revision: str | Sequence[str] | None = "c2a4f8a5cf72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mapping of old action names to new action names
ACTION_RENAMES = {
    "core.table.search_records": "core.table.search_rows",
}


def upgrade() -> None:
    """Rename core.table.search_records references in database."""

    action_case_parts = []
    for old_name, new_name in ACTION_RENAMES.items():
        action_case_parts.append(f"WHEN '{old_name}' THEN '{new_name}'")
    action_case_sql = "\n                ".join(action_case_parts)

    old_names_list = "', '".join(ACTION_RENAMES.keys())
    op.execute(
        f"""
        UPDATE action
        SET type = CASE type
            {action_case_sql}
        END
        WHERE type IN ('{old_names_list}')
        """
    )

    node_case_parts = []
    for old_name, new_name in ACTION_RENAMES.items():
        node_case_parts.append(
            f"WHEN node_item.node->'data'->>'type' = '{old_name}' "
            f"THEN jsonb_set(node_item.node, '{{data,type}}', '\"{new_name}\"'::jsonb)"
        )
    node_case_sql = "\n                            ".join(node_case_parts)

    op.execute(
        f"""
        UPDATE workflow
        SET object = jsonb_set(
            object,
            '{{nodes}}',
            (
                SELECT jsonb_agg(
                    CASE
                        {node_case_sql}
                        ELSE node_item.node
                    END
                    ORDER BY node_item.ord
                )
                FROM jsonb_array_elements(object->'nodes') WITH ORDINALITY AS node_item(node, ord)
            )
        )
        WHERE object IS NOT NULL
        AND object ? 'nodes'
        AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements(object->'nodes') AS node
            WHERE node->'data'->>'type' IN ('{old_names_list}')
        )
        """
    )

    action_def_case_parts = []
    for old_name, new_name in ACTION_RENAMES.items():
        action_def_case_parts.append(
            f"WHEN action_item.action->>'action' = '{old_name}' "
            f"THEN jsonb_set(action_item.action, '{{action}}', '\"{new_name}\"'::jsonb)"
        )
    action_def_case_sql = "\n                            ".join(action_def_case_parts)

    op.execute(
        f"""
        UPDATE workflowdefinition wd
        SET content = jsonb_set(
            content,
            '{{actions}}',
            (
                SELECT jsonb_agg(
                    CASE
                        {action_def_case_sql}
                        ELSE action_item.action
                    END
                    ORDER BY action_item.ord
                )
                FROM jsonb_array_elements(content->'actions') WITH ORDINALITY AS action_item(action, ord)
            )
        )
        FROM workflow w
        WHERE wd.workflow_id = w.id
        AND wd.version = w.version
        AND wd.content IS NOT NULL
        AND wd.content ? 'actions'
        AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements(wd.content->'actions') AS action
            WHERE action->>'action' IN ('{old_names_list}')
        )
        """
    )


def downgrade() -> None:
    """Revert core.table.search_rows references back to core.table.search_records."""

    reverse_mapping = {new: old for old, new in ACTION_RENAMES.items()}

    action_case_parts = []
    for new_name, old_name in reverse_mapping.items():
        action_case_parts.append(f"WHEN '{new_name}' THEN '{old_name}'")
    action_case_sql = "\n                ".join(action_case_parts)

    new_names_list = "', '".join(reverse_mapping.keys())
    op.execute(
        f"""
        UPDATE action
        SET type = CASE type
            {action_case_sql}
        END
        WHERE type IN ('{new_names_list}')
        """
    )

    node_case_parts = []
    for new_name, old_name in reverse_mapping.items():
        node_case_parts.append(
            f"WHEN node_item.node->'data'->>'type' = '{new_name}' "
            f"THEN jsonb_set(node_item.node, '{{data,type}}', '\"{old_name}\"'::jsonb)"
        )
    node_case_sql = "\n                            ".join(node_case_parts)

    op.execute(
        f"""
        UPDATE workflow
        SET object = jsonb_set(
            object,
            '{{nodes}}',
            (
                SELECT jsonb_agg(
                    CASE
                        {node_case_sql}
                        ELSE node_item.node
                    END
                    ORDER BY node_item.ord
                )
                FROM jsonb_array_elements(object->'nodes') WITH ORDINALITY AS node_item(node, ord)
            )
        )
        WHERE object IS NOT NULL
        AND object ? 'nodes'
        AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements(object->'nodes') AS node
            WHERE node->'data'->>'type' IN ('{new_names_list}')
        )
        """
    )

    action_def_case_parts = []
    for new_name, old_name in reverse_mapping.items():
        action_def_case_parts.append(
            f"WHEN action_item.action->>'action' = '{new_name}' "
            f"THEN jsonb_set(action_item.action, '{{action}}', '\"{old_name}\"'::jsonb)"
        )
    action_def_case_sql = "\n                            ".join(action_def_case_parts)

    op.execute(
        f"""
        UPDATE workflowdefinition wd
        SET content = jsonb_set(
            content,
            '{{actions}}',
            (
                SELECT jsonb_agg(
                    CASE
                        {action_def_case_sql}
                        ELSE action_item.action
                    END
                    ORDER BY action_item.ord
                )
                FROM jsonb_array_elements(content->'actions') WITH ORDINALITY AS action_item(action, ord)
            )
        )
        FROM workflow w
        WHERE wd.workflow_id = w.id
        AND wd.version = w.version
        AND wd.content IS NOT NULL
        AND wd.content ? 'actions'
        AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements(wd.content->'actions') AS action
            WHERE action->>'action' IN ('{new_names_list}')
        )
        """
    )
