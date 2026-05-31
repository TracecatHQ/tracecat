from tracecat.agent.session.types import AgentSessionEntity
from tracecat.chat.tools import get_default_tools


def test_workspace_chat_defaults_include_table_authoring_tools() -> None:
    default_tools = set(get_default_tools(AgentSessionEntity.WORKSPACE_CHAT.value))

    assert {
        "core.table.update_table",
        "core.table.create_column",
        "core.table.update_column",
        "core.table.delete_column",
    } <= default_tools
