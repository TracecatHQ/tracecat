"""add cursor pagination indexes for performance

Revision ID: 9c50a7f1ba96
Revises: 71c8649f752f
Create Date: 2025-06-23 23:10:46.844705

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c50a7f1ba96"
down_revision: str | None = "71c8649f752f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cursor pagination indexes for tables with timestamp fields."""
    # Create indexes for cursor pagination performance

    # Core workflow-related tables
    op.create_index(
        "idx_workflows_cursor_pagination",
        "workflow",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_actions_cursor_pagination",
        "action",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_workflow_definitions_cursor_pagination",
        "workflowdefinition",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_webhooks_cursor_pagination",
        "webhook",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_schedules_cursor_pagination",
        "schedule",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    # Case management tables
    op.create_index(
        "idx_cases_cursor_pagination",
        "cases",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_case_comments_cursor_pagination",
        "case_comments",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_case_events_cursor_pagination",
        "case_event",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_case_fields_cursor_pagination",
        "case_fields",
        [sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    # Organization and workspace tables
    op.create_index(
        "idx_workspaces_cursor_pagination",
        "workspace",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_secrets_cursor_pagination",
        "secret",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_tags_cursor_pagination",
        "tag",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_workflow_folders_cursor_pagination",
        "workflow_folder",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    # Registry tables
    op.create_index(
        "idx_registry_repositories_cursor_pagination",
        "registryrepository",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_registry_actions_cursor_pagination",
        "registryaction",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    # Tables and interaction tables
    op.create_index(
        "idx_tables_cursor_pagination",
        "tables",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    op.create_index(
        "idx_interactions_cursor_pagination",
        "interaction",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    # Organization settings (special case with different owner structure)
    op.create_index(
        "idx_organization_settings_cursor_pagination",
        "organization_settings",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )

    # Tables without owner_id but with timestamp fields
    op.create_index(
        "idx_table_columns_cursor_pagination",
        "table_columns",
        [sa.text("created_at DESC"), sa.text("id DESC")],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Remove cursor pagination indexes."""
    # Drop all the indexes created in upgrade
    op.drop_index("idx_workflows_cursor_pagination", "workflow", if_exists=True)
    op.drop_index("idx_actions_cursor_pagination", "action", if_exists=True)
    op.drop_index(
        "idx_workflow_definitions_cursor_pagination",
        "workflowdefinition",
        if_exists=True,
    )
    op.drop_index("idx_webhooks_cursor_pagination", "webhook", if_exists=True)
    op.drop_index("idx_schedules_cursor_pagination", "schedule", if_exists=True)
    op.drop_index("idx_cases_cursor_pagination", "cases", if_exists=True)
    op.drop_index(
        "idx_case_comments_cursor_pagination", "case_comments", if_exists=True
    )
    op.drop_index("idx_case_events_cursor_pagination", "case_event", if_exists=True)
    op.drop_index("idx_case_fields_cursor_pagination", "case_fields", if_exists=True)
    op.drop_index("idx_workspaces_cursor_pagination", "workspace", if_exists=True)
    op.drop_index("idx_secrets_cursor_pagination", "secret", if_exists=True)
    op.drop_index("idx_tags_cursor_pagination", "tag", if_exists=True)
    op.drop_index(
        "idx_workflow_folders_cursor_pagination", "workflow_folder", if_exists=True
    )
    op.drop_index(
        "idx_registry_repositories_cursor_pagination",
        "registryrepository",
        if_exists=True,
    )
    op.drop_index(
        "idx_registry_actions_cursor_pagination", "registryaction", if_exists=True
    )
    op.drop_index("idx_tables_cursor_pagination", "tables", if_exists=True)
    op.drop_index("idx_interactions_cursor_pagination", "interaction", if_exists=True)
    op.drop_index(
        "idx_organization_settings_cursor_pagination",
        "organization_settings",
        if_exists=True,
    )
    op.drop_index(
        "idx_table_columns_cursor_pagination", "table_columns", if_exists=True
    )
