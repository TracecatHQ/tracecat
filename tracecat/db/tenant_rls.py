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
    "case_comment_mention",
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
    "invitation",
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
    "case_agent_session_interaction",
    "case_table_row",
    "case_version",
    "case_comment_agent_invocation",
    "agent_channel_token",
    "agent_preset_version",
    "agent_folder",
    "agent_tag",
    "skill_folder",
    "skill_tag",
    "skill",
    "skill_blob",
    "skill_upload",
    "skill_draft_file",
    "skill_version",
    "skill_version_file",
    "skill_version_tool",
    "skill_version_mcp_tool",
    "agent_preset_skill",
    "agent_preset_version_skill",
    "workspace_sync_resource_mapping",
)

POST_RLS_ORG_SCOPED_TABLES = (
    "watchtower_agent",
    "mcp_refresh_token",
    "agent_custom_provider",
    "external_user",
    "external_group",
    "external_group_mapping",
    "scim_connection",
)

POST_RLS_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES = (
    "invitation_grant",
    "watchtower_agent_session",
    "watchtower_agent_tool_call",
    "service_account",
    "mcp_personal_access_token",
    "agent_model_access",
)

SPECIAL_TENANT_POLICY_TABLES = frozenset(
    {
        "agent_tag_link",
        "skill_tag_link",
        "service_account_api_key",
        "service_account_scope",
        "external_group_member",
    }
)

# Workspace and oauth_state carry custom policy SQL. scope and agent_catalog
# both have nullable organization_id and allow shared platform-owned rows.
SEARCH_POLICY_TABLES = frozenset(
    {
        "search_workspace_state",
        "search_embedding_config",
        "search_collection",
        "search_document",
        "search_chunk",
    }
)
# group_member.organization_id is a nullable denormalization for the membership
# foreign key; the table is governed by its parent group's policy.
SPECIAL_WORKSPACE_POLICY_TABLES = frozenset({"oauth_state"}) | SEARCH_POLICY_TABLES
SPECIAL_ORG_POLICY_TABLES = (
    frozenset({"workspace", "scope", "agent_catalog", "group_member"})
    | SEARCH_POLICY_TABLES
)

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


def enable_group_member_table_rls() -> str:
    """Scope legacy nullable membership rows through their owning group."""
    return f"""
        ALTER TABLE group_member ENABLE ROW LEVEL SECURITY;
        CREATE POLICY {policy_name("group_member")} ON group_member
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR EXISTS (
                    SELECT 1 FROM "group" AS parent_group
                    WHERE parent_group.id = group_member.group_id
                      AND parent_group.organization_id = NULLIF(
                          current_setting('app.current_org_id', true), ''
                      )::uuid
                )
            );
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


def enable_external_group_member_table_rls() -> str:
    # No tenant column of its own, so isolation goes through the parent row.
    # external_group is org-scoped only; there is no workspace dimension.
    org_condition = """
                EXISTS (
                    SELECT 1
                    FROM external_group
                    WHERE external_group.id = "external_group_member".external_group_id
                      AND external_group.organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                )
    """
    return f"""
        ALTER TABLE "external_group_member" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("external_group_member")} ON "external_group_member"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
{org_condition}
                )
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
{org_condition}
                )
            );
    """


def disable_external_group_member_table_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name("external_group_member")} ON "external_group_member";
        ALTER TABLE "external_group_member" DISABLE ROW LEVEL SECURITY;
    """


def _agent_tag_link_workspace_condition() -> str:
    return """
                EXISTS (
                    SELECT 1
                    FROM agent_tag
                    WHERE agent_tag.id = agent_tag_link.tag_id
                      AND agent_tag.workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                )
                AND EXISTS (
                    SELECT 1
                    FROM agent_preset
                    WHERE agent_preset.id = agent_tag_link.preset_id
                      AND agent_preset.workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                )
    """


def enable_agent_tag_link_table_rls() -> str:
    workspace_condition = _agent_tag_link_workspace_condition()
    return f"""
        ALTER TABLE "agent_tag_link" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("agent_tag_link")} ON "agent_tag_link"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
{workspace_condition}
                )
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
{workspace_condition}
                )
            );
    """


def disable_agent_tag_link_table_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name("agent_tag_link")} ON "agent_tag_link";
        ALTER TABLE "agent_tag_link" DISABLE ROW LEVEL SECURITY;
    """


def _skill_tag_link_workspace_condition() -> str:
    return """
                EXISTS (
                    SELECT 1
                    FROM skill_tag
                    WHERE skill_tag.id = skill_tag_link.tag_id
                      AND skill_tag.workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                )
                AND EXISTS (
                    SELECT 1
                    FROM skill
                    WHERE skill.id = skill_tag_link.skill_id
                      AND skill.workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                )
    """


def enable_skill_tag_link_table_rls() -> str:
    workspace_condition = _skill_tag_link_workspace_condition()
    return f"""
        ALTER TABLE "skill_tag_link" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name("skill_tag_link")} ON "skill_tag_link"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
{workspace_condition}
                )
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
{workspace_condition}
                )
            );
    """


def disable_skill_tag_link_table_rls() -> str:
    return f"""
        DROP POLICY IF EXISTS {policy_name("skill_tag_link")} ON "skill_tag_link";
        ALTER TABLE "skill_tag_link" DISABLE ROW LEVEL SECURITY;
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


def enable_search_table_rls(table: str) -> str:
    """Enforce both tenant identities and hide data after workspace deletion."""
    if table not in SEARCH_POLICY_TABLES:
        raise ValueError("Unknown search table")
    predicate = f"""
        current_setting('app.rls_bypass', true) = 'on'
        OR (
            workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
            AND organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            AND EXISTS (SELECT 1 FROM workspace w
                        WHERE w.id = "{table}".workspace_id
                          AND w.organization_id = "{table}".organization_id)
        )
    """
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;
        CREATE POLICY {policy_name(table)} ON "{table}"
        FOR ALL USING ({predicate}) WITH CHECK ({predicate});
    """
