"""Rename owner_id to workspace_id/organization_id.

This migration renames the `owner_id` column to be more semantically meaningful:
- workspace_id for workspace-scoped resources
- organization_id for organization-scoped resources (workspace, organization_secret, organization_settings, registry_repository, registry_action)

Revision ID: a1b2c3d4e5f6
Revises: f4695a7728a8
Create Date: 2025-12-03 20:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f4695a7728a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that should have organization_id (org-scoped)
ORG_SCOPED_TABLES = [
    "workspace",
    "organization_secret",
    "organization_settings",
    "registry_action",
    "registry_repository",
]

# Tables that should have workspace_id (workspace-scoped) with FK to workspace
WORKSPACE_SCOPED_TABLES_WITH_FK = [
    "action",
    "approval",
    "case_comment",
    "case_event",
    "case_task",
    "chat",
    "chat_message",
    "file",
    "interaction",
    "schedule",
    "tables",
    "webhook",
    "webhook_api_key",
    "workflow_definition",
]

# Tables that already have workspace_id with FK (just need rename)
WORKSPACE_SCOPED_TABLES_EXISTING_FK = [
    "agent_preset",
    "case",
    "case_duration",
    "case_duration_definition",
    "case_field",
    "case_tag",
    "mcp_integration",
    "oauth_integration",
    "oauth_provider",
    "secret",
    "tag",
    "workflow",
    "workflow_folder",
    "workspace_variable",
]

# Unique constraints to rename (old_name, new_name, table)
UNIQUE_CONSTRAINTS_TO_RENAME = [
    ("uq_agent_preset_owner_slug", "uq_agent_preset_workspace_slug", "agent_preset"),
    (
        "uq_approval_owner_session_tool",
        "uq_approval_workspace_session_tool",
        "approval",
    ),
    (
        "uq_case_duration_definition_owner_name",
        "uq_case_duration_definition_workspace_name",
        "case_duration_definition",
    ),
    ("uq_case_field_owner", "uq_case_field_workspace", "case_field"),
    ("uq_case_tag_name_owner", "uq_case_tag_name_workspace", "case_tag"),
    ("uq_case_tag_ref_owner", "uq_case_tag_ref_workspace", "case_tag"),
    (
        "uq_mcp_integration_owner_slug",
        "uq_mcp_integration_workspace_slug",
        "mcp_integration",
    ),
    (
        "uq_oauth_integration_owner_provider_user_flow",
        "uq_oauth_integration_workspace_provider_user_flow",
        "oauth_integration",
    ),
    (
        "uq_oauth_provider_owner_provider_grant_type",
        "uq_oauth_provider_workspace_provider_grant_type",
        "oauth_provider",
    ),
    (
        "uq_secret_name_environment_owner_id",
        "uq_secret_name_environment_workspace_id",
        "secret",
    ),
    ("uq_tables_owner_id_name", "uq_tables_workspace_id_name", "tables"),
    ("uq_tag_name_owner", "uq_tag_name_workspace", "tag"),
    ("uq_tag_ref_owner", "uq_tag_ref_workspace", "tag"),
    ("uq_workflow_alias_owner_id", "uq_workflow_alias_workspace_id", "workflow"),
    (
        "uq_workflow_folder_path_owner",
        "uq_workflow_folder_path_workspace",
        "workflow_folder",
    ),
    (
        "uq_workspace_variable_name_environment_owner_id",
        "uq_workspace_variable_name_environment_workspace_id",
        "workspace_variable",
    ),
]

# Index to rename
INDEX_TO_RENAME = [
    ("ix_case_field_owner_id", "ix_case_field_workspace_id", "case_field"),
]


def upgrade() -> None:
    # 1. Rename owner_id to organization_id for org-scoped tables
    for table in ORG_SCOPED_TABLES:
        op.alter_column(table, "owner_id", new_column_name="organization_id")

    # 2. Rename owner_id to workspace_id for workspace-scoped tables (no FK yet)
    for table in WORKSPACE_SCOPED_TABLES_WITH_FK:
        op.alter_column(table, "owner_id", new_column_name="workspace_id")

    # 3. Rename owner_id to workspace_id for tables that already have FK
    for table in WORKSPACE_SCOPED_TABLES_EXISTING_FK:
        op.alter_column(table, "owner_id", new_column_name="workspace_id")

    # 4. Add FK constraints for workspace_id columns that need them
    for table in WORKSPACE_SCOPED_TABLES_WITH_FK:
        op.create_foreign_key(
            f"fk_{table}_workspace_id_workspace",
            table,
            "workspace",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # 5. Rename unique constraints
    for old_name, new_name, table in UNIQUE_CONSTRAINTS_TO_RENAME:
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"'
        )

    # 6. Rename indexes
    for old_name, new_name, _table in INDEX_TO_RENAME:
        op.execute(f'ALTER INDEX "{old_name}" RENAME TO "{new_name}"')


def downgrade() -> None:
    # 1. Rename indexes back
    for old_name, new_name, _table in INDEX_TO_RENAME:
        op.execute(f'ALTER INDEX "{new_name}" RENAME TO "{old_name}"')

    # 2. Rename unique constraints back
    for old_name, new_name, table in UNIQUE_CONSTRAINTS_TO_RENAME:
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{new_name}" TO "{old_name}"'
        )

    # 3. Drop FK constraints for workspace_id columns
    for table in WORKSPACE_SCOPED_TABLES_WITH_FK:
        op.drop_constraint(
            f"fk_{table}_workspace_id_workspace", table, type_="foreignkey"
        )

    # 4. Rename workspace_id back to owner_id for tables that had FK
    for table in WORKSPACE_SCOPED_TABLES_EXISTING_FK:
        op.alter_column(table, "workspace_id", new_column_name="owner_id")

    # 5. Rename workspace_id back to owner_id for workspace-scoped tables
    for table in WORKSPACE_SCOPED_TABLES_WITH_FK:
        op.alter_column(table, "workspace_id", new_column_name="owner_id")

    # 6. Rename organization_id back to owner_id for org-scoped tables
    for table in ORG_SCOPED_TABLES:
        op.alter_column(table, "organization_id", new_column_name="owner_id")
