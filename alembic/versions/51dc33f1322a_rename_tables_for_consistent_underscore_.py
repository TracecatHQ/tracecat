"""rename tables for consistent underscore delimiters

Revision ID: 51dc33f1322a
Revises: 287584da65f6
Create Date: 2025-12-02 11:26:05.675636

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "51dc33f1322a"
down_revision: str | None = "287584da65f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Table renames for underscore consistency: (old_name, new_name)
UNDERSCORE_RENAMES = [
    ("oauthaccount", "oauth_account"),
    ("accesstoken", "access_token"),
    ("organizationsecret", "organization_secret"),
    ("workflowdefinition", "workflow_definition"),
    ("workflowtag", "workflow_tag"),
    ("registryrepository", "registry_repository"),
    ("registryaction", "registry_action"),
]

# Table renames for singular naming: (old_name, new_name)
# Note: 'tables' kept plural as 'table' is a SQL reserved keyword
# Note: 'case' is also a SQL reserved keyword and must be quoted
PLURALITY_RENAMES = [
    ("cases", "case"),
    ("table_columns", "table_column"),
    ("case_fields", "case_field"),
    ("case_comments", "case_comment"),
    ("case_tasks", "case_task"),
]


def upgrade() -> None:
    # ===================
    # UNDERSCORE RENAMES
    # ===================

    for old_name, new_name in UNDERSCORE_RENAMES:
        op.rename_table(old_name, new_name)

    # Primary key constraints
    op.execute(
        "ALTER TABLE oauth_account RENAME CONSTRAINT "
        "oauthaccount_pkey TO oauth_account_pkey"
    )
    op.execute(
        "ALTER TABLE access_token RENAME CONSTRAINT "
        "accesstoken_pkey TO access_token_pkey"
    )
    op.execute(
        "ALTER TABLE organization_secret RENAME CONSTRAINT "
        "organizationsecret_pkey TO organization_secret_pkey"
    )
    op.execute(
        "ALTER TABLE workflow_definition RENAME CONSTRAINT "
        "workflowdefinition_pkey TO workflow_definition_pkey"
    )
    op.execute(
        "ALTER TABLE workflow_tag RENAME CONSTRAINT "
        "workflowtag_pkey TO workflow_tag_pkey"
    )
    op.execute(
        "ALTER TABLE registry_repository RENAME CONSTRAINT "
        "registryrepository_pkey TO registry_repository_pkey"
    )
    op.execute(
        "ALTER TABLE registry_action RENAME CONSTRAINT "
        "registryaction_pkey TO registry_action_pkey"
    )

    # Unique constraints
    op.execute(
        "ALTER TABLE access_token RENAME CONSTRAINT "
        "uq_accesstoken_id TO uq_access_token_id"
    )
    op.execute(
        "ALTER TABLE organization_secret RENAME CONSTRAINT "
        "organizationsecret_name_environment_key TO organization_secret_name_environment_key"
    )
    op.execute(
        "ALTER TABLE registry_repository RENAME CONSTRAINT "
        "registryrepository_id_key TO registry_repository_id_key"
    )
    op.execute(
        "ALTER TABLE registry_repository RENAME CONSTRAINT "
        "registryrepository_origin_key TO registry_repository_origin_key"
    )
    op.execute(
        "ALTER TABLE registry_action RENAME CONSTRAINT "
        "registryaction_id_key TO registry_action_id_key"
    )

    # Indexes
    op.execute(
        "ALTER INDEX ix_oauthaccount_account_id RENAME TO ix_oauth_account_account_id"
    )
    op.execute(
        "ALTER INDEX ix_oauthaccount_oauth_name RENAME TO ix_oauth_account_oauth_name"
    )
    op.execute(
        "ALTER INDEX ix_accesstoken_created_at RENAME TO ix_access_token_created_at"
    )
    op.execute(
        "ALTER INDEX ix_organizationsecret_id RENAME TO ix_organization_secret_id"
    )
    op.execute(
        "ALTER INDEX ix_organizationsecret_name RENAME TO ix_organization_secret_name"
    )
    op.execute(
        "ALTER INDEX ix_workflowdefinition_id RENAME TO ix_workflow_definition_id"
    )
    op.execute(
        "ALTER INDEX ix_workflowdefinition_version RENAME TO ix_workflow_definition_version"
    )

    # Foreign key constraints
    op.execute(
        "ALTER TABLE oauth_account RENAME CONSTRAINT "
        "oauthaccount_user_id_fkey TO oauth_account_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE access_token RENAME CONSTRAINT "
        "accesstoken_user_id_fkey TO access_token_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE workflow_definition RENAME CONSTRAINT "
        "workflowdefinition_workflow_id_fkey TO workflow_definition_workflow_id_fkey"
    )
    op.execute(
        "ALTER TABLE workflow_tag RENAME CONSTRAINT "
        "workflowtag_tag_id_fkey TO workflow_tag_tag_id_fkey"
    )
    op.execute(
        "ALTER TABLE workflow_tag RENAME CONSTRAINT "
        "workflowtag_workflow_id_fkey TO workflow_tag_workflow_id_fkey"
    )
    op.execute(
        "ALTER TABLE registry_action RENAME CONSTRAINT "
        "registryaction_repository_id_fkey TO registry_action_repository_id_fkey"
    )

    # ===================
    # PLURALITY RENAMES
    # ===================

    for old_name, new_name in PLURALITY_RENAMES:
        op.rename_table(old_name, new_name)

    # Primary key constraints (note: "case" is a reserved keyword)
    op.execute('ALTER TABLE "case" RENAME CONSTRAINT cases_pkey TO case_pkey')
    op.execute(
        "ALTER TABLE table_column RENAME CONSTRAINT "
        "table_columns_pkey TO table_column_pkey"
    )
    op.execute(
        "ALTER TABLE case_field RENAME CONSTRAINT case_fields_pkey TO case_field_pkey"
    )
    op.execute(
        "ALTER TABLE case_comment RENAME CONSTRAINT "
        "case_comments_pkey TO case_comment_pkey"
    )
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT case_tasks_pkey TO case_task_pkey"
    )

    # Unique constraints
    op.execute(
        "ALTER TABLE table_column RENAME CONSTRAINT "
        "table_columns_table_id_name_key TO table_column_table_id_name_key"
    )
    op.execute(
        "ALTER TABLE case_field RENAME CONSTRAINT "
        "uq_case_fields_owner TO uq_case_field_owner"
    )

    # Indexes
    op.execute("ALTER INDEX ix_cases_id RENAME TO ix_case_id")
    op.execute("ALTER INDEX ix_cases_case_number RENAME TO ix_case_case_number")
    op.execute(
        "ALTER INDEX idx_case_cursor_pagination RENAME TO ix_case_cursor_pagination"
    )
    op.execute("ALTER INDEX ix_table_columns_id RENAME TO ix_table_column_id")
    op.execute("ALTER INDEX ix_table_columns_name RENAME TO ix_table_column_name")
    op.execute("ALTER INDEX ix_case_fields_id RENAME TO ix_case_field_id")
    op.execute("ALTER INDEX ix_case_fields_owner_id RENAME TO ix_case_field_owner_id")
    op.execute("ALTER INDEX ix_case_comments_id RENAME TO ix_case_comment_id")
    op.execute("ALTER INDEX ix_case_tasks_id RENAME TO ix_case_task_id")

    # Foreign key constraints
    op.execute(
        'ALTER TABLE "case" RENAME CONSTRAINT '
        "fk_cases_owner_id_workspace TO fk_case_owner_id_workspace"
    )
    # Note: fk_case_assignee_id already uses correct naming
    op.execute(
        "ALTER TABLE table_column RENAME CONSTRAINT "
        "table_columns_table_id_fkey TO table_column_table_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_field RENAME CONSTRAINT "
        "fk_case_fields_owner_id TO fk_case_field_owner_id"
    )
    op.execute(
        "ALTER TABLE case_comment RENAME CONSTRAINT "
        "case_comments_case_id_fkey TO case_comment_case_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT "
        "case_tasks_case_id_fkey TO case_task_case_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT "
        "case_tasks_assignee_id_fkey TO case_task_assignee_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT "
        "case_tasks_workflow_id_fkey TO case_task_workflow_id_fkey"
    )

    # Update FK references from other tables pointing to 'cases'
    op.execute(
        "ALTER TABLE case_tag_link RENAME CONSTRAINT "
        "casetag_case_id_fkey TO fk_case_tag_link_case_id"
    )
    op.execute(
        "ALTER TABLE case_event RENAME CONSTRAINT "
        "case_event_case_id_fkey TO fk_case_event_case_id"
    )
    op.execute(
        "ALTER TABLE case_duration RENAME CONSTRAINT "
        "case_duration_case_id_fkey TO fk_case_duration_case_id"
    )
    op.execute(
        "ALTER TABLE case_attachment RENAME CONSTRAINT "
        "case_attachment_case_id_fkey TO fk_case_attachment_case_id"
    )


def downgrade() -> None:
    # ===================
    # REVERT PLURALITY RENAMES
    # ===================

    # Revert FK references from other tables
    op.execute(
        "ALTER TABLE case_attachment RENAME CONSTRAINT "
        "fk_case_attachment_case_id TO case_attachment_case_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_duration RENAME CONSTRAINT "
        "fk_case_duration_case_id TO case_duration_case_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_event RENAME CONSTRAINT "
        "fk_case_event_case_id TO case_event_case_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_tag_link RENAME CONSTRAINT "
        "fk_case_tag_link_case_id TO casetag_case_id_fkey"
    )

    # Revert foreign key constraints
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT "
        "case_task_workflow_id_fkey TO case_tasks_workflow_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT "
        "case_task_assignee_id_fkey TO case_tasks_assignee_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT "
        "case_task_case_id_fkey TO case_tasks_case_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_comment RENAME CONSTRAINT "
        "case_comment_case_id_fkey TO case_comments_case_id_fkey"
    )
    op.execute(
        "ALTER TABLE case_field RENAME CONSTRAINT "
        "fk_case_field_owner_id TO fk_case_fields_owner_id"
    )
    op.execute(
        "ALTER TABLE table_column RENAME CONSTRAINT "
        "table_column_table_id_fkey TO table_columns_table_id_fkey"
    )
    # Note: fk_case_assignee_id already uses correct naming
    op.execute(
        'ALTER TABLE "case" RENAME CONSTRAINT '
        "fk_case_owner_id_workspace TO fk_cases_owner_id_workspace"
    )

    # Revert indexes
    op.execute("ALTER INDEX ix_case_task_id RENAME TO ix_case_tasks_id")
    op.execute("ALTER INDEX ix_case_comment_id RENAME TO ix_case_comments_id")
    op.execute("ALTER INDEX ix_case_field_owner_id RENAME TO ix_case_fields_owner_id")
    op.execute("ALTER INDEX ix_case_field_id RENAME TO ix_case_fields_id")
    op.execute("ALTER INDEX ix_table_column_name RENAME TO ix_table_columns_name")
    op.execute("ALTER INDEX ix_table_column_id RENAME TO ix_table_columns_id")
    op.execute(
        "ALTER INDEX ix_case_cursor_pagination RENAME TO idx_case_cursor_pagination"
    )
    op.execute("ALTER INDEX ix_case_case_number RENAME TO ix_cases_case_number")
    op.execute("ALTER INDEX ix_case_id RENAME TO ix_cases_id")

    # Revert unique constraints
    op.execute(
        "ALTER TABLE case_field RENAME CONSTRAINT "
        "uq_case_field_owner TO uq_case_fields_owner"
    )
    op.execute(
        "ALTER TABLE table_column RENAME CONSTRAINT "
        "table_column_table_id_name_key TO table_columns_table_id_name_key"
    )

    # Revert primary key constraints
    op.execute(
        "ALTER TABLE case_task RENAME CONSTRAINT case_task_pkey TO case_tasks_pkey"
    )
    op.execute(
        "ALTER TABLE case_comment RENAME CONSTRAINT "
        "case_comment_pkey TO case_comments_pkey"
    )
    op.execute(
        "ALTER TABLE case_field RENAME CONSTRAINT case_field_pkey TO case_fields_pkey"
    )
    op.execute(
        "ALTER TABLE table_column RENAME CONSTRAINT "
        "table_column_pkey TO table_columns_pkey"
    )
    op.execute('ALTER TABLE "case" RENAME CONSTRAINT case_pkey TO cases_pkey')

    # Revert table names
    for old_name, new_name in reversed(PLURALITY_RENAMES):
        op.rename_table(new_name, old_name)

    # ===================
    # REVERT UNDERSCORE RENAMES
    # ===================

    # Revert foreign key constraints
    op.execute(
        "ALTER TABLE registry_action RENAME CONSTRAINT "
        "registry_action_repository_id_fkey TO registryaction_repository_id_fkey"
    )
    op.execute(
        "ALTER TABLE workflow_tag RENAME CONSTRAINT "
        "workflow_tag_workflow_id_fkey TO workflowtag_workflow_id_fkey"
    )
    op.execute(
        "ALTER TABLE workflow_tag RENAME CONSTRAINT "
        "workflow_tag_tag_id_fkey TO workflowtag_tag_id_fkey"
    )
    op.execute(
        "ALTER TABLE workflow_definition RENAME CONSTRAINT "
        "workflow_definition_workflow_id_fkey TO workflowdefinition_workflow_id_fkey"
    )
    op.execute(
        "ALTER TABLE access_token RENAME CONSTRAINT "
        "access_token_user_id_fkey TO accesstoken_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE oauth_account RENAME CONSTRAINT "
        "oauth_account_user_id_fkey TO oauthaccount_user_id_fkey"
    )

    # Revert indexes
    op.execute(
        "ALTER INDEX ix_workflow_definition_version RENAME TO ix_workflowdefinition_version"
    )
    op.execute(
        "ALTER INDEX ix_workflow_definition_id RENAME TO ix_workflowdefinition_id"
    )
    op.execute(
        "ALTER INDEX ix_organization_secret_name RENAME TO ix_organizationsecret_name"
    )
    op.execute(
        "ALTER INDEX ix_organization_secret_id RENAME TO ix_organizationsecret_id"
    )
    op.execute(
        "ALTER INDEX ix_access_token_created_at RENAME TO ix_accesstoken_created_at"
    )
    op.execute(
        "ALTER INDEX ix_oauth_account_oauth_name RENAME TO ix_oauthaccount_oauth_name"
    )
    op.execute(
        "ALTER INDEX ix_oauth_account_account_id RENAME TO ix_oauthaccount_account_id"
    )

    # Revert unique constraints
    op.execute(
        "ALTER TABLE registry_action RENAME CONSTRAINT "
        "registry_action_id_key TO registryaction_id_key"
    )
    op.execute(
        "ALTER TABLE registry_repository RENAME CONSTRAINT "
        "registry_repository_origin_key TO registryrepository_origin_key"
    )
    op.execute(
        "ALTER TABLE registry_repository RENAME CONSTRAINT "
        "registry_repository_id_key TO registryrepository_id_key"
    )
    op.execute(
        "ALTER TABLE organization_secret RENAME CONSTRAINT "
        "organization_secret_name_environment_key TO organizationsecret_name_environment_key"
    )
    op.execute(
        "ALTER TABLE access_token RENAME CONSTRAINT "
        "uq_access_token_id TO uq_accesstoken_id"
    )

    # Revert primary key constraints
    op.execute(
        "ALTER TABLE registry_action RENAME CONSTRAINT "
        "registry_action_pkey TO registryaction_pkey"
    )
    op.execute(
        "ALTER TABLE registry_repository RENAME CONSTRAINT "
        "registry_repository_pkey TO registryrepository_pkey"
    )
    op.execute(
        "ALTER TABLE workflow_tag RENAME CONSTRAINT "
        "workflow_tag_pkey TO workflowtag_pkey"
    )
    op.execute(
        "ALTER TABLE workflow_definition RENAME CONSTRAINT "
        "workflow_definition_pkey TO workflowdefinition_pkey"
    )
    op.execute(
        "ALTER TABLE organization_secret RENAME CONSTRAINT "
        "organization_secret_pkey TO organizationsecret_pkey"
    )
    op.execute(
        "ALTER TABLE access_token RENAME CONSTRAINT "
        "access_token_pkey TO accesstoken_pkey"
    )
    op.execute(
        "ALTER TABLE oauth_account RENAME CONSTRAINT "
        "oauth_account_pkey TO oauthaccount_pkey"
    )

    # Revert table names
    for old_name, new_name in reversed(UNDERSCORE_RENAMES):
        op.rename_table(new_name, old_name)
