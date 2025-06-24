"""add cursor pagination indexes for performance

Revision ID: 9c50a7f1ba96
Revises: 71c8649f752f
Create Date: 2025-06-23 23:10:46.844705

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c50a7f1ba96"
down_revision: str | None = "71c8649f752f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cursor pagination indexes for tables with timestamp fields."""
    # Use raw SQL for CONCURRENTLY indexes since op.create_index doesn't support it

    # Core workflow-related tables
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflows_cursor_pagination "
        "ON workflow(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_actions_cursor_pagination "
        "ON action(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflow_definitions_cursor_pagination "
        "ON workflowdefinition(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_webhooks_cursor_pagination "
        "ON webhook(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_schedules_cursor_pagination "
        "ON schedule(owner_id, created_at DESC, id DESC);"
    )

    # Case management tables
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cases_cursor_pagination "
        "ON cases(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_case_comments_cursor_pagination "
        "ON case_comments(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_case_events_cursor_pagination "
        "ON case_event(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_case_fields_cursor_pagination "
        "ON case_fields(created_at DESC, id DESC);"
    )

    # Organization and workspace tables
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workspaces_cursor_pagination "
        "ON workspace(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_secrets_cursor_pagination "
        "ON secret(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tags_cursor_pagination "
        "ON tag(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflow_folders_cursor_pagination "
        "ON workflow_folder(owner_id, created_at DESC, id DESC);"
    )

    # Registry tables
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_registry_repositories_cursor_pagination "
        "ON registryrepository(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_registry_actions_cursor_pagination "
        "ON registryaction(owner_id, created_at DESC, id DESC);"
    )

    # Tables and interaction tables
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tables_cursor_pagination "
        "ON tables(owner_id, created_at DESC, id DESC);"
    )

    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_interactions_cursor_pagination "
        "ON interaction(owner_id, created_at DESC, id DESC);"
    )

    # Organization settings (special case with different owner structure)
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_organization_settings_cursor_pagination "
        "ON organization_settings(owner_id, created_at DESC, id DESC);"
    )

    # Tables without owner_id but with timestamp fields
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_table_columns_cursor_pagination "
        "ON table_columns(created_at DESC, id DESC);"
    )


def downgrade() -> None:
    """Remove cursor pagination indexes."""
    # Drop all the indexes created in upgrade
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_workflows_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_actions_cursor_pagination;")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_workflow_definitions_cursor_pagination;"
    )
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_webhooks_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_schedules_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_cases_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_case_comments_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_case_events_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_case_fields_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_workspaces_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_secrets_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_tags_cursor_pagination;")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_workflow_folders_cursor_pagination;"
    )
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_registry_repositories_cursor_pagination;"
    )
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_registry_actions_cursor_pagination;"
    )
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_tables_cursor_pagination;")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_interactions_cursor_pagination;")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_organization_settings_cursor_pagination;"
    )
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_table_columns_cursor_pagination;")
