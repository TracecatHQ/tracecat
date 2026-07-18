# Store default tools for each entity type
from tracecat.agent.mcp.internal_tools import (
    AGENT_SESSION_SEARCH_INTERNAL_TOOL_NAMES,
    BUILDER_INTERNAL_TOOL_NAMES,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope

WORKSPACE_CHAT_AGENT_DEFAULT_TOOLS = [
    "ai.agent.create_preset",
    "ai.agent.get_preset",
    "ai.agent.list_presets",
    "ai.agent.update_preset",
]

WORKSPACE_CHAT_BASE_DEFAULT_TOOLS = [
    "core.table.list_tables",
    "core.table.get_table_metadata",
    "core.table.create_table",
    "core.table.update_table",
    "core.table.create_column",
    "core.table.update_column",
    "core.table.delete_column",
    "core.table.lookup",
    "core.table.lookup_many",
    "core.table.is_in",
    "core.table.search_rows",
    "core.table.insert_row",
    "core.table.insert_rows",
    "core.table.update_row",
    "core.table.delete_row",
    "core.table.download",
    "core.cases.create_case",
    "core.cases.update_case",
    "core.cases.delete_case",
    "core.cases.list_cases",
    "core.cases.get_case",
    "core.cases.search_cases",
    "core.workflow.create_workflow",
    "core.workflow.get_workflow",
    "core.workflow.edit_workflow",
    "core.workflow.get_authoring_context",
    "core.workflow.get_webhook",
    "core.workflow.update_webhook",
    "core.workflow.get_case_trigger",
    "core.workflow.update_case_trigger",
    "core.workflow.publish",
    "core.workflow.run",
    "core.workflow.execute",
]

WORKSPACE_CHAT_DEFAULT_TOOLS = [
    *WORKSPACE_CHAT_AGENT_DEFAULT_TOOLS,
    *WORKSPACE_CHAT_BASE_DEFAULT_TOOLS,
    *AGENT_SESSION_SEARCH_INTERNAL_TOOL_NAMES,
]

TOOL_DEFAULTS = {
    AgentSessionEntity.CASE: [
        "core.cases.get_case",
        "core.cases.list_cases",
        "core.cases.update_case",
        "core.cases.create_comment",
        "core.cases.list_comments",
        *AGENT_SESSION_SEARCH_INTERNAL_TOOL_NAMES,
    ],
    AgentSessionEntity.AGENT_PRESET: [],
    AgentSessionEntity.AGENT_PRESET_BUILDER: BUILDER_INTERNAL_TOOL_NAMES,
    AgentSessionEntity.WORKSPACE_CHAT: WORKSPACE_CHAT_DEFAULT_TOOLS,
}


def filter_workspace_chat_tools_for_entitlements(
    tools: list[str],
    *,
    agent_addons_enabled: bool,
) -> list[str]:
    """Filter Workspace chat default tools by enabled entitlements."""
    if agent_addons_enabled:
        return list(tools)

    blocked_defaults = set(WORKSPACE_CHAT_AGENT_DEFAULT_TOOLS)
    return [tool for tool in tools if tool not in blocked_defaults]


def filter_workspace_chat_tools_for_scopes(
    tools: list[str],
    *,
    role: Role,
) -> list[str]:
    """Drop chat tools the caller's RBAC does not authorize them to execute.

    Chat tools run through the executor, which authorizes the underlying action
    against the ``tracecat-executor`` service principal (whose allowlist includes
    ``action:*:execute``), NOT against the chat user's RBAC. The user only passes
    one scope gate -- ``agent:execute`` at session start -- so without this filter
    ``agent:execute`` would implicitly let the agent run any action on the user's
    behalf, including creating agents, editing workflows, or deleting cases.

    Every registry action carries an ``action:{action_key}:execute`` scope (see
    ``_seed_registry_scopes``), and the executor enforces exactly that scope via
    :func:`require_action_scope` at dispatch time. We mirror that check here --
    the last point where the user's real role is available -- so a tool is only
    offered when the user could execute the same action directly. The match is
    dynamic (derived from the tool name, no hardcoded list) and uses the same
    :func:`has_scope` semantics as the executor, including the ``"*"`` superuser
    bypass and wildcard grants (``action:*:execute``, ``action:core.*:execute``).
    """
    granted = role.scopes or frozenset()
    return [
        tool
        for tool in tools
        # Internal tools are not registry actions and carry no action scope.
        # Only the generally-available recall set may pass; privileged internal
        # tools (e.g. builder tools) are granted by entity type, not requested.
        if tool in AGENT_SESSION_SEARCH_INTERNAL_TOOL_NAMES
        or has_scope(granted, f"action:{tool}:execute")
    ]


def get_default_tools(
    entity_type: str,
    *,
    agent_addons_enabled: bool = True,
) -> list[str]:
    """Get default tools for an entity type."""
    entity = AgentSessionEntity(entity_type)
    tools = TOOL_DEFAULTS.get(entity, [])
    if entity is AgentSessionEntity.WORKSPACE_CHAT:
        return filter_workspace_chat_tools_for_entitlements(
            tools,
            agent_addons_enabled=agent_addons_enabled,
        )
    return list(tools)
