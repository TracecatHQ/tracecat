import uuid

from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.chat.tools import (
    WORKSPACE_CHAT_AGENT_DEFAULT_TOOLS,
    filter_workspace_chat_tools_for_scopes,
    get_default_tools,
)


def _role(*scopes: str) -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset(scopes),
    )


def test_workspace_chat_default_tools_include_authoring_actions() -> None:
    tools = get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value)

    assert tools == [
        "ai.agent.create_preset",
        "ai.agent.get_preset",
        "ai.agent.list_presets",
        "ai.agent.update_preset",
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


def test_workspace_chat_default_tools_exclude_agent_actions_without_entitlement() -> (
    None
):
    tools = get_default_tools(
        AgentSessionEntity.WORKSPACE_CHAT.value,
        agent_addons_enabled=False,
    )

    assert all(tool not in tools for tool in WORKSPACE_CHAT_AGENT_DEFAULT_TOOLS)
    assert "core.table.list_tables" in tools
    assert "core.cases.list_cases" in tools


def test_scope_filter_strips_all_tools_without_action_execute() -> None:
    # A role that can start chat (agent:execute) but holds no action:*:execute
    # must not be offered ANY tool -- otherwise agent:execute would let the agent
    # run actions (create agents, edit workflows, delete cases) the user cannot.
    tools = get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value)
    filtered = filter_workspace_chat_tools_for_scopes(
        tools, role=_role("agent:execute")
    )

    assert filtered == []


def test_scope_filter_action_wildcard_keeps_everything() -> None:
    # Editors/admins hold action:*:execute and keep the full tool set.
    tools = get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value)
    filtered = filter_workspace_chat_tools_for_scopes(
        tools, role=_role("agent:execute", "action:*:execute")
    )

    assert filtered == tools


def test_scope_filter_namespace_wildcard_gates_by_namespace() -> None:
    # A custom role scoped to core actions keeps core.* tools but is denied the
    # ai.agent.* tools -- "I should not be able to create agents without perms."
    tools = get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value)
    filtered = filter_workspace_chat_tools_for_scopes(
        tools, role=_role("agent:execute", "action:core.*:execute")
    )

    assert "core.workflow.edit_workflow" in filtered
    assert "core.table.list_tables" in filtered
    assert "core.cases.delete_case" in filtered
    # Agent-preset authoring tools require action:ai.agent.*:execute.
    assert not any(tool.startswith("ai.agent.") for tool in filtered)


def test_scope_filter_specific_action_scope_keeps_only_that_tool() -> None:
    tools = get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value)
    filtered = filter_workspace_chat_tools_for_scopes(
        tools,
        role=_role("agent:execute", "action:core.workflow.edit_workflow:execute"),
    )

    assert filtered == ["core.workflow.edit_workflow"]


def test_scope_filter_none_scopes_strips_all_tools() -> None:
    # An unresolved/empty scope set denies every tool, matching the executor.
    role = Role(
        type="user",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=None,
    )
    tools = get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value)
    filtered = filter_workspace_chat_tools_for_scopes(tools, role=role)

    assert filtered == []


def test_scope_filter_superuser_wildcard_keeps_everything() -> None:
    tools = get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value)
    filtered = filter_workspace_chat_tools_for_scopes(tools, role=_role("*"))

    assert filtered == tools


def test_scope_filter_gates_extras_the_same_way() -> None:
    # Extras the user added in the tools dialog are gated identically -- a user
    # without action:ai.agent.*:execute cannot smuggle create_preset in via extras.
    extras = ["core.table.list_tables", "ai.agent.create_preset"]
    filtered = filter_workspace_chat_tools_for_scopes(
        extras, role=_role("agent:execute", "action:core.*:execute")
    )

    assert filtered == ["core.table.list_tables"]
