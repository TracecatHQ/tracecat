# Store default tools for each entity type
from tracecat.agent.mcp.internal_tools import BUILDER_INTERNAL_TOOL_NAMES
from tracecat.agent.session.types import AgentSessionEntity

WORKSPACE_CHAT_DEFAULT_TOOLS = [
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
]

TOOL_DEFAULTS = {
    AgentSessionEntity.CASE: [
        "core.cases.get_case",
        "core.cases.list_cases",
        "core.cases.update_case",
        "core.cases.create_comment",
        "core.cases.list_comments",
    ],
    AgentSessionEntity.AGENT_PRESET: [],
    AgentSessionEntity.AGENT_PRESET_BUILDER: BUILDER_INTERNAL_TOOL_NAMES,
    AgentSessionEntity.WORKSPACE_CHAT: WORKSPACE_CHAT_DEFAULT_TOOLS,
}


def get_default_tools(entity_type: str) -> list[str]:
    """Get default tools for an entity type."""
    return TOOL_DEFAULTS.get(AgentSessionEntity(entity_type), [])
