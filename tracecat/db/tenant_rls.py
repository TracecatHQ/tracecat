"""Shared tenant RLS policy registry and SQL helpers.

Any new tenant-scoped table must be added to one of the policy registries
below and its creating Alembic migration should apply the matching helper SQL
in the same revision.
"""

from __future__ import annotations

RLS_BYPASS_VAR = "app.rls_bypass"
RLS_BYPASS_ON = "on"

# Tables covered by the initial RLS rollout migration.
INITIAL_WORKSPACE_SCOPED_TABLES = (
    "workflow",
    "workflow_definition",
    "workflow_folder",
    "action",
    "webhook",
    "webhook_api_key",
    "schedule",
    "secret",
    "workspace_variable",
    "tables",
    "case",
    "case_comment",
    "case_event",
    "case_task",
    "case_tag",
    "case_field",
    "case_duration_definition",
    "case_duration",
    "case_trigger",
    "case_dropdown_definition",
    "workflow_tag",
    "interaction",
    "approval",
    "agent_session",
    "agent_session_history",
    "agent_preset",
    "file",
    "chat",
    "chat_message",
    "membership",
    "invitation",
    "oauth_integration",
    "oauth_provider",
    "mcp_integration",
)

INITIAL_ORG_SCOPED_TABLES = (
    "organization_secret",
    "organization_settings",
    "organization_domain",
    "organization_membership",
    "organization_invitation",
    "organization_tier",
    "registry_repository",
    "registry_action",
    "registry_version",
    "registry_index",
    "role",
    "group",
)

INITIAL_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES = (
    "user_role_assignment",
    "group_role_assignment",
)

# Tables introduced after the initial RLS rollout. Their creating or follow-up
# revisions must apply policy SQL explicitly.
POST_RLS_WORKSPACE_SCOPED_TABLES = (
    "case_table_row",
    "agent_channel_token",
    "agent_preset_version",
    "skill",
    "skill_blob",
    "skill_upload",
    "skill_draft_file",
    "skill_version",
    "skill_version_file",
    "agent_preset_skill",
    "agent_preset_version_skill",
)

POST_RLS_ORG_SCOPED_TABLES = (
    "watchtower_agent",
    "mcp_refresh_token",
    "agent_custom_provider",
)

POST_RLS_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES = (
    "watchtower_agent_session",
    "watchtower_agent_tool_call",
    "service_account",
    "agent_model_access",
)

SPECIAL_TENANT_POLICY_TABLES = frozenset(
    {"service_account_api_key", "service_account_scope"}
)

# Workspace and oauth_state carry custom policy SQL. scope and agent_catalog
# both have nullable organization_id and allow shared platform-owned rows.
SPECIAL_WORKSPACE_POLICY_TABLES = frozenset({"oauth_state"})
SPECIAL_ORG_POLICY_TABLES = frozenset({"workspace", "scope", "agent_catalog"})

CURRENT_WORKSPACE_SCOPED_TABLES = (
    *INITIAL_WORKSPACE_SCOPED_TABLES,
    *POST_RLS_WORKSPACE_SCOPED_TABLES,
)
CURRENT_ORG_SCOPED_TABLES = (
    *INITIAL_ORG_SCOPED_TABLES,
    *POST_RLS_ORG_SCOPED_TABLES,
)
CURRENT_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES = (
    *INITIAL_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES,
    *POST_RLS_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES,
)

WORKSPACE_POLICY_TABLES = frozenset(CURRENT_WORKSPACE_SCOPED_TABLES)
ORG_POLICY_TABLES = frozenset(CURRENT_ORG_SCOPED_TABLES)
ORG_OPTIONAL_WORKSPACE_POLICY_TABLES = frozenset(
    CURRENT_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES
)
ALL_TENANT_RLS_TABLES = (
    WORKSPACE_POLICY_TABLES
    | ORG_POLICY_TABLES
    | ORG_OPTIONAL_WORKSPACE_POLICY_TABLES
    | SPECIAL_WORKSPACE_POLICY_TABLES
    | SPECIAL_ORG_POLICY_TABLES
    | SPECIAL_TENANT_POLICY_TABLES
)


def policy_name(table: str) -> str:
    return f"rls_policy_{table}"


def enable_workspace_table_rls(table: str) -> str:
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name(table)} ON "{table}"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
            );
    """


def disable_workspace_table_rls(table: str) -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name(table)} ON "{table}";
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
    """


def enable_oauth_state_special_rls() -> str:
    return f"""
        ALTER TABLE "oauth_state" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("oauth_state")} ON "oauth_state"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
                    user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                    AND workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                )
            );
    """


def disable_oauth_state_special_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name("oauth_state")} ON "oauth_state";
        ALTER TABLE "oauth_state" DISABLE ROW LEVEL SECURITY;
    """


def enable_org_table_rls(table: str) -> str:
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name(table)} ON "{table}"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            );
    """


def disable_org_table_rls(table: str) -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name(table)} ON "{table}";
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
    """


def enable_org_optional_workspace_table_rls(table: str) -> str:
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name(table)} ON "{table}"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
                    organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                    AND (
                        workspace_id IS NULL
                        OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                        OR NULLIF(current_setting('app.current_workspace_id', true), '')::uuid IS NULL
                    )
                )
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
                    organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                    AND (
                        workspace_id IS NULL
                        OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                        OR NULLIF(current_setting('app.current_workspace_id', true), '')::uuid IS NULL
                    )
                )
            );
    """


def disable_org_optional_workspace_table_rls(table: str) -> str:
    return disable_org_table_rls(table)


def enable_scope_table_rls() -> str:
    return f"""
        ALTER TABLE "scope" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("scope")} ON "scope"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id IS NULL
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            );
    """


def disable_scope_table_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name("scope")} ON "scope";
        ALTER TABLE "scope" DISABLE ROW LEVEL SECURITY;
    """


def enable_service_account_child_table_rls(table: str) -> str:
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name(table)} ON "{table}"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR EXISTS (
                    SELECT 1
                    FROM service_account
                    WHERE service_account.id = "{table}".service_account_id
                      AND service_account.organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                      AND (
                          service_account.workspace_id IS NULL
                          OR service_account.workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                          OR NULLIF(current_setting('app.current_workspace_id', true), '')::uuid IS NULL
                      )
                )
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR EXISTS (
                    SELECT 1
                    FROM service_account
                    WHERE service_account.id = "{table}".service_account_id
                      AND service_account.organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                      AND (
                          service_account.workspace_id IS NULL
                          OR service_account.workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                          OR NULLIF(current_setting('app.current_workspace_id', true), '')::uuid IS NULL
                      )
                )
    """

def enable_agent_catalog_table_rls() -> str:
    return f"""
        ALTER TABLE "agent_catalog" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("agent_catalog")} ON "agent_catalog"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id IS NULL
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            );
    """


def _agent_catalog_platform_read_policy() -> str:
    return f"{policy_name('agent_catalog')}_platform_read"


def enable_agent_catalog_table_rls() -> str:
    # Split policy so platform rows (organization_id IS NULL) are readable by
    # every org but cannot be written or deleted by an org-scoped session.
    # Writes/deletes are gated by the FOR ALL policy, which only matches
    # rows owned by the current org; platform-row read access is granted
    # additively via a FOR SELECT policy (permissive policies OR together).
    return f"""
        ALTER TABLE "agent_catalog" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("agent_catalog")} ON "agent_catalog"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            );

        CREATE POLICY {_agent_catalog_platform_read_policy()} ON "agent_catalog"
            FOR SELECT
            USING (
                organization_id IS NULL
            );
    """


def disable_service_account_child_table_rls(table: str) -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name(table)} ON "{table}";
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
    """

def disable_agent_catalog_table_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name("agent_catalog")} ON "agent_catalog";
        ALTER TABLE "agent_catalog" DISABLE ROW LEVEL SECURITY;
    """


def disable_agent_catalog_table_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {_agent_catalog_platform_read_policy()} ON "agent_catalog";
        DROP POLICY IF EXISTS {policy_name("agent_catalog")} ON "agent_catalog";
        ALTER TABLE "agent_catalog" DISABLE ROW LEVEL SECURITY;
    """


def enable_workspace_special_rls() -> str:
    return f"""
        ALTER TABLE "workspace" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("workspace")} ON "workspace"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                OR organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            );
    """


def disable_workspace_special_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name("workspace")} ON "workspace";
        ALTER TABLE "workspace" DISABLE ROW LEVEL SECURITY;
    """
