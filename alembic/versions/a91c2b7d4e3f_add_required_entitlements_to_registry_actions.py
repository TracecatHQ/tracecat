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


TARGET_TABLES = (
    "registry_action",
    "platform_registry_action",
    "registry_index",
    "platform_registry_index",
)


CASE_ACTIONS = (
    "create_task",
    "get_task",
    "list_tasks",
    "update_task",
    "delete_task",
    "get_case_metrics",
)


def _sql_in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    case_names_sql = _sql_in(CASE_ACTIONS)
    for table in TARGET_TABLES:
        op.execute(
            f"""
            UPDATE {table}
            SET options = COALESCE(options, '{{}}'::jsonb) ||
                jsonb_build_object(
                    'required_entitlements',
                    CASE
                        WHEN namespace = 'core.cases' AND name IN ({case_names_sql})
                            THEN jsonb_build_array('case_addons')
                        WHEN namespace = 'ai' AND name = 'preset_agent'
                            THEN jsonb_build_array('agent_addons')
                    END
                )
            WHERE (namespace = 'core.cases' AND name IN ({case_names_sql}))
               OR (namespace = 'ai' AND name = 'preset_agent')
            """
        )


def downgrade() -> None:
    case_names_sql = _sql_in(CASE_ACTIONS)
    for table in TARGET_TABLES:
        op.execute(
            f"""
            UPDATE {table}
            SET options = COALESCE(options, '{{}}'::jsonb) - 'required_entitlements'
            WHERE (namespace = 'core.cases' AND name IN ({case_names_sql}))
               OR (namespace = 'ai' AND name = 'preset_agent')
            """
        )
