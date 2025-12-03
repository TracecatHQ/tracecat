"""standardize naming convention

Revision ID: d2eb2dcbb369
Revises: 7b9dbaecb03a
Create Date: 2025-12-03 11:18:56.612694

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2eb2dcbb369"
down_revision: str | None = "7b9dbaecb03a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table_name, old_constraint_name, new_constraint_name)
CONSTRAINT_RENAMES: list[tuple[str, str, str]] = [
    # Primary keys: {table}_pkey -> pk_{table}
    ("access_token", "access_token_pkey", "pk_access_token"),
    ("action", "action_pkey", "pk_action"),
    ("agent_preset", "agent_preset_pkey", "pk_agent_preset"),
    ("approval", "approval_pkey", "pk_approval"),
    ("case", "case_pkey", "pk_case"),
    ("case_attachment", "case_attachment_pkey", "pk_case_attachment"),
    ("case_comment", "case_comment_pkey", "pk_case_comment"),
    ("case_duration", "case_duration_pkey", "pk_case_duration"),
    (
        "case_duration_definition",
        "case_duration_definition_pkey",
        "pk_case_duration_definition",
    ),
    ("case_event", "case_event_pkey", "pk_case_event"),
    ("case_field", "case_field_pkey", "pk_case_field"),
    ("case_tag", "case_tag_pkey", "pk_case_tag"),
    ("case_tag_link", "casetag_pkey", "pk_case_tag_link"),
    ("case_task", "case_task_pkey", "pk_case_task"),
    ("chat", "chat_pkey", "pk_chat"),
    ("chat_message", "chat_message_pkey", "pk_chat_message"),
    ("file", "file_pkey", "pk_file"),
    ("interaction", "interaction_pkey", "pk_interaction"),
    ("membership", "membership_pkey", "pk_membership"),
    ("oauth_account", "oauth_account_pkey", "pk_oauth_account"),
    ("oauth_integration", "oauth_integration_pkey", "pk_oauth_integration"),
    ("oauth_provider", "oauth_provider_pkey", "pk_oauth_provider"),
    ("oauth_state", "oauth_state_pkey", "pk_oauth_state"),
    ("organization_secret", "organization_secret_pkey", "pk_organization_secret"),
    ("organization_settings", "organization_settings_pkey", "pk_organization_settings"),
    ("ownership", "ownership_pkey", "pk_ownership"),
    ("registry_action", "registry_action_pkey", "pk_registry_action"),
    ("registry_repository", "registry_repository_pkey", "pk_registry_repository"),
    ("saml_request_data", "saml_request_data_pkey", "pk_saml_request_data"),
    ("schedule", "schedule_pkey", "pk_schedule"),
    ("secret", "secret_pkey", "pk_secret"),
    ("table_column", "table_column_pkey", "pk_table_column"),
    ("tables", "tables_pkey", "pk_tables"),
    ("tag", "tag_pkey", "pk_tag"),
    ("user", "user_pkey", "pk_user"),
    ("webhook", "webhook_pkey", "pk_webhook"),
    ("webhook_api_key", "webhook_api_key_pkey", "pk_webhook_api_key"),
    ("workflow", "workflow_pkey", "pk_workflow"),
    ("workflow_definition", "workflow_definition_pkey", "pk_workflow_definition"),
    ("workflow_folder", "workflow_folder_pkey", "pk_workflow_folder"),
    ("workflow_tag", "workflow_tag_pkey", "pk_workflow_tag"),
    ("workspace", "workspace_pkey", "pk_workspace"),
    ("workspace_variable", "workspace_variable_pkey", "pk_workspace_variable"),
    # Unique constraints
    (
        "organization_secret",
        "organization_secret_name_environment_key",
        "uq_organization_secret_name_environment",
    ),
    ("registry_action", "registry_action_id_key", "uq_registry_action_id"),
    ("registry_repository", "registry_repository_id_key", "uq_registry_repository_id"),
    (
        "registry_repository",
        "registry_repository_origin_key",
        "uq_registry_repository_origin",
    ),
    ("secret", "uq_secret_name_env_owner", "uq_secret_name_environment_owner_id"),
    ("table_column", "table_column_table_id_name_key", "uq_table_column_table_id_name"),
    ("tables", "tables_name_owner_id_key", "uq_tables_owner_id_name"),
    (
        "webhook_api_key",
        "webhook_api_key_webhook_id_key",
        "uq_webhook_api_key_webhook_id",
    ),
    ("workspace", "workspace_id_key", "uq_workspace_id"),
    (
        "workspace_variable",
        "workspace_variable_name_environment_owner_id_key",
        "uq_workspace_variable_name_environment_owner_id",
    ),
]


def upgrade() -> None:
    for table, old_name, new_name in CONSTRAINT_RENAMES:
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"'
        )


def downgrade() -> None:
    for table, old_name, new_name in reversed(CONSTRAINT_RENAMES):
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{new_name}" TO "{old_name}"'
        )
