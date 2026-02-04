"""Add required entitlements metadata for registry actions

Revision ID: a91c2b7d4e3f
Revises: 6b1d2e4f8c01
Create Date: 2026-02-04 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a91c2b7d4e3f"
down_revision: str | None = "6b1d2e4f8c01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Case tasks UDFs
    op.execute(
        """
        UPDATE registry_action
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_tasks'))
        WHERE namespace = 'core.cases'
          AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task')
        """
    )
    op.execute(
        """
        UPDATE platform_registry_action
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_tasks'))
        WHERE namespace = 'core.cases'
          AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task')
        """
    )
    op.execute(
        """
        UPDATE registry_index
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_tasks'))
        WHERE namespace = 'core.cases'
          AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task')
        """
    )
    op.execute(
        """
        UPDATE platform_registry_index
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_tasks'))
        WHERE namespace = 'core.cases'
          AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task')
        """
    )

    # Case durations UDFs
    op.execute(
        """
        UPDATE registry_action
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_durations'))
        WHERE namespace = 'core.cases'
          AND name = 'get_case_metrics'
        """
    )
    op.execute(
        """
        UPDATE platform_registry_action
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_durations'))
        WHERE namespace = 'core.cases'
          AND name = 'get_case_metrics'
        """
    )
    op.execute(
        """
        UPDATE registry_index
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_durations'))
        WHERE namespace = 'core.cases'
          AND name = 'get_case_metrics'
        """
    )
    op.execute(
        """
        UPDATE platform_registry_index
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('case_durations'))
        WHERE namespace = 'core.cases'
          AND name = 'get_case_metrics'
        """
    )

    # Agent presets UDF
    op.execute(
        """
        UPDATE registry_action
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('agent_presets'))
        WHERE namespace = 'ai'
          AND name = 'preset_agent'
        """
    )
    op.execute(
        """
        UPDATE platform_registry_action
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('agent_presets'))
        WHERE namespace = 'ai'
          AND name = 'preset_agent'
        """
    )
    op.execute(
        """
        UPDATE registry_index
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('agent_presets'))
        WHERE namespace = 'ai'
          AND name = 'preset_agent'
        """
    )
    op.execute(
        """
        UPDATE platform_registry_index
        SET options = COALESCE(options, '{}'::jsonb) ||
            jsonb_build_object('required_entitlements', jsonb_build_array('agent_presets'))
        WHERE namespace = 'ai'
          AND name = 'preset_agent'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE registry_action
        SET options = COALESCE(options, '{}'::jsonb) - 'required_entitlements'
        WHERE (namespace = 'core.cases'
               AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task', 'get_case_metrics'))
           OR (namespace = 'ai' AND name = 'preset_agent')
        """
    )
    op.execute(
        """
        UPDATE platform_registry_action
        SET options = COALESCE(options, '{}'::jsonb) - 'required_entitlements'
        WHERE (namespace = 'core.cases'
               AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task', 'get_case_metrics'))
           OR (namespace = 'ai' AND name = 'preset_agent')
        """
    )
    op.execute(
        """
        UPDATE registry_index
        SET options = COALESCE(options, '{}'::jsonb) - 'required_entitlements'
        WHERE (namespace = 'core.cases'
               AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task', 'get_case_metrics'))
           OR (namespace = 'ai' AND name = 'preset_agent')
        """
    )
    op.execute(
        """
        UPDATE platform_registry_index
        SET options = COALESCE(options, '{}'::jsonb) - 'required_entitlements'
        WHERE (namespace = 'core.cases'
               AND name IN ('create_task', 'get_task', 'list_tasks', 'update_task', 'delete_task', 'get_case_metrics'))
           OR (namespace = 'ai' AND name = 'preset_agent')
        """
    )
