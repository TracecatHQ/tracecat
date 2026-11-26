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
    "agent_channel_bot",
    "agent_channel_route",
    "agent_session_thread",
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
    "skill_library_install",
    "agent_preset_skill",
    "agent_preset_version_skill",
    "workspace_sync_resource_mapping",
    "form",
    "form_submission",
    "form_view",
    "form_file",
    "workspace_tunnel",
    "agent_session_command",
    "workspace_sync_operation",
    "case_search_word",
    "table_folder",
    "managed_detached_copy",
    "case_trigger_outbox",
    "case_view",
    "case_view_pin",
    "agent_preset_approver",
    "event_source",
    "trigger",
    "object_storage_trigger",
    "trigger_binding",
    "event_outbox",
)

POST_RLS_ORG_SCOPED_TABLES = (
    "watchtower_agent",
    "mcp_refresh_token",
    "agent_custom_provider",
    "organization_secret_store",
    "external_user",
    "external_group",
    "external_group_mapping",
    "external_group_member",
    "scim_connection",
    "invitation_group",
    "agent_channel_user",
)

POST_RLS_ORG_OPTIONAL_WORKSPACE_SCOPED_TABLES = (
    "invitation_grant",
    "watchtower_agent_session",
    "watchtower_agent_tool_call",
    "service_account",
    "mcp_personal_access_token",
    "agent_model_access",
    "workspace_secret_store_authorization",
)

SPECIAL_TENANT_POLICY_TABLES = frozenset(
    {
        "agent_tag_link",
        "skill_tag_link",
        "service_account_api_key",
        "service_account_scope",
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
# Managed rollout state: tenant sessions may only read their workspace's rows;
# writes require the RLS-bypass session that platform rollouts use.
READ_ONLY_WORKSPACE_POLICY_TABLES = frozenset(
    {
        "managed_resource",
        "managed_target",
        "managed_reference_match",
        "managed_rollout",
        "managed_preset_skill_binding",
    }
)
# group_member.organization_id is a nullable denormalization for the membership
# foreign key; the table is governed by its parent group's policy.
SPECIAL_WORKSPACE_POLICY_TABLES = (
    frozenset({"oauth_state"})
    | SEARCH_POLICY_TABLES
    | READ_ONLY_WORKSPACE_POLICY_TABLES
)
SPECIAL_ORG_POLICY_TABLES = (
    frozenset({"workspace", "scope", "agent_catalog", "group_member"})
    | SEARCH_POLICY_TABLES
)

_CURRENT_WORKSPACE_ID = (
    "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
)
_CURRENT_ORG_ID = "NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def _parent_in_workspace(table: str, column: str, parent: str) -> str:
    return f"""EXISTS (
                    SELECT 1 FROM "{parent}" AS parent
                    WHERE parent.id = "{table}".{column}
                      AND parent.workspace_id = {_CURRENT_WORKSPACE_ID}
                )"""


# Tables with no tenant column of their own. Each row belongs to the tenant
# of the parent rows it references, so every policy requires all of them to
# be in the current workspace (or organization). A dropdown value's option
# must belong to the value's own definition. ownership has no foreign keys:
# its owner_id is the owning organization.
PARENT_SCOPED_TABLE_CONDITIONS: dict[str, str] = {
    "case_attachment": f"""{_parent_in_workspace("case_attachment", "case_id", "case")}
                AND {_parent_in_workspace("case_attachment", "file_id", "file")}""",
    "case_dropdown_option": _parent_in_workspace(
        "case_dropdown_option", "definition_id", "case_dropdown_definition"
    ),
    "case_dropdown_value": f"""{_parent_in_workspace("case_dropdown_value", "case_id", "case")}
                AND {_parent_in_workspace("case_dropdown_value", "definition_id", "case_dropdown_definition")}
                AND (
                    case_dropdown_value.option_id IS NULL
                    OR EXISTS (
                        SELECT 1
                        FROM case_dropdown_option AS parent
                        WHERE parent.id = case_dropdown_value.option_id
                          AND parent.definition_id = case_dropdown_value.definition_id
                    )
                )""",
    "case_tag_link": f"""{_parent_in_workspace("case_tag_link", "case_id", "case")}
                AND {_parent_in_workspace("case_tag_link", "tag_id", "case_tag")}""",
    "workflow_tag_link": f"""{_parent_in_workspace("workflow_tag_link", "workflow_id", "workflow")}
                AND {_parent_in_workspace("workflow_tag_link", "tag_id", "workflow_tag")}""",
    "table_column": _parent_in_workspace("table_column", "table_id", "tables"),
    "role_scope": f"""EXISTS (
                    SELECT 1 FROM "role" AS parent
                    WHERE parent.id = role_scope.role_id
                      AND parent.organization_id = {_CURRENT_ORG_ID}
                )
                AND EXISTS (
                    SELECT 1 FROM scope AS parent
                    WHERE parent.id = role_scope.scope_id
                      AND (
                          parent.organization_id IS NULL
                          OR parent.organization_id = {_CURRENT_ORG_ID}
                      )
                )""",
    "ownership": f"ownership.owner_id = {_CURRENT_ORG_ID}",
}
PARENT_SCOPED_TABLES = frozenset(PARENT_SCOPED_TABLE_CONDITIONS)

# Public tables that deliberately have no RLS, with the reason for each.
GLOBAL_TABLES: dict[str, str] = {
    "alembic_version": "Migration bookkeeping; holds no tenant data.",
    "user": "Users can belong to several organizations.",
    # The next four hold credentials (session tokens, IdP tokens, MFA
    # factors). They are user-scoped, not org-scoped; isolating them needs a
    # user-keyed policy (app.current_user_id), not a tenant one.
    "access_token": "Login session tokens belong to a user, not an organization.",
    "oauth_account": "IdP tokens for a user's login identity, not an organization.",
    "mfa_recovery_code": "Hashed MFA recovery codes belong to a user.",
    "webauthn_credential": "MFA credentials belong to a user, not an organization.",
    "saml_request_data": "Read before login, when no tenant is known yet.",
    "organization": "The tenant root; resolved before a tenant is known.",
    "tier": "Platform-wide plan definitions shared by every organization.",
    "platform_registry_repository": "Platform registry shared by every organization.",
    "platform_registry_version": "Platform registry shared by every organization.",
    "platform_registry_action": "Platform registry shared by every organization.",
    "platform_registry_index": "Platform registry shared by every organization.",
    "platform_secret": "Platform-owned secrets, read only by platform code.",
    "platform_settings": "Platform-wide settings shared by every organization.",
}

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
    | PARENT_SCOPED_TABLES
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


def enable_parent_scoped_table_rls(table: str) -> str:
    condition = PARENT_SCOPED_TABLE_CONDITIONS[table]
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name(table)} ON "{table}"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
                {condition}
                )
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
                OR (
                {condition}
                )
            );
    """


def disable_parent_scoped_table_rls(table: str) -> str:
    if table not in PARENT_SCOPED_TABLES:
        raise ValueError(f"Unknown parent-scoped table: {table}")
    return f"""
        DROP POLICY IF EXISTS {policy_name(table)} ON "{table}";
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
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


def _workspace_read_policy(table: str) -> str:
    return f"{policy_name(table)}_workspace_read"


def enable_workspace_read_only_table_rls(table: str) -> str:
    # Split policy, as for agent_catalog: the FOR ALL policy admits only the
    # RLS-bypass session, so tenant sessions cannot insert, update or delete.
    # The additive FOR SELECT policy lets a tenant read its workspace's rows.
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;

        CREATE POLICY {policy_name(table)} ON "{table}"
            FOR ALL
            USING (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
            )
            WITH CHECK (
                current_setting('{RLS_BYPASS_VAR}', true) = '{RLS_BYPASS_ON}'
            );

        CREATE POLICY {_workspace_read_policy(table)} ON "{table}"
            FOR SELECT
            USING (
                workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
            );
    """


def disable_workspace_read_only_table_rls(table: str) -> str:
    return f"""
        DROP POLICY IF EXISTS {_workspace_read_policy(table)} ON "{table}";
        DROP POLICY IF EXISTS {policy_name(table)} ON "{table}";
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
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
