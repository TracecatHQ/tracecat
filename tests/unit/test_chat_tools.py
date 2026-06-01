from tracecat.agent.session.types import AgentSessionEntity
from tracecat.chat.tools import WORKSPACE_CHAT_AGENT_DEFAULT_TOOLS, get_default_tools


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
