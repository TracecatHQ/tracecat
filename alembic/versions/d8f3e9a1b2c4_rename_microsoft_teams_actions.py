"""rename_microsoft_teams_actions

Revision ID: d8f3e9a1b2c4
Revises: 3c55aa60ca2c
Create Date: 2025-09-30 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8f3e9a1b2c4"
down_revision: str | None = "3c55aa60ca2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mapping of old action names to new action names
ACTION_RENAMES = {
    "tools.microsoft_teams.send_teams_message": "tools.microsoft_teams.send_message",
    "tools.microsoft_teams.reply_teams_message": "tools.microsoft_teams.reply_message",
    "tools.microsoft_teams.create_teams_channel": "tools.microsoft_teams.create_channel",
    "tools.microsoft_teams.delete_teams_channel": "tools.microsoft_teams.delete_channel",
    "tools.microsoft_teams.send_teams_buttons": "tools.microsoft_teams.send_buttons",
}


def upgrade() -> None:
    """Rename Microsoft Teams action references in database."""

    # Build CASE statements for efficient single-pass updates
    action_case_parts = []
    for old_name, new_name in ACTION_RENAMES.items():
        action_case_parts.append(f"WHEN '{old_name}' THEN '{new_name}'")
    action_case_sql = "\n                ".join(action_case_parts)

    # 1. Update action.type column (single query for all renames)
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

    # 2. Update workflow.object column (single query for all renames)
    # Build CASE statement for node type replacements
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

    # 3. Update workflowdefinition.content column (single query for all renames)
    # OPTIMIZATION: Only update the latest version of each workflow definition
    # This uses workflow.version to identify which definition is current
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
    """Revert Microsoft Teams action names back to original names."""

    # Reverse the mapping for downgrade
    reverse_mapping = {new: old for old, new in ACTION_RENAMES.items()}

    # Build CASE statements for efficient single-pass updates
    action_case_parts = []
    for new_name, old_name in reverse_mapping.items():
        action_case_parts.append(f"WHEN '{new_name}' THEN '{old_name}'")
    action_case_sql = "\n                ".join(action_case_parts)

    # 1. Update action.type column (single query for all renames)
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

    # 2. Update workflow.object column (single query for all renames)
    # Build CASE statement for node type replacements
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

    # 3. Update workflowdefinition.content column (single query for all renames)
    # OPTIMIZATION: Only update the latest version of each workflow definition
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
