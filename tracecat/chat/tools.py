# Store default tools for each entity type
from tracecat.agent.builder.tools import AGENT_PRESET_BUILDER_TOOL_NAMES
from tracecat.chat.enums import ChatEntity

TOOL_DEFAULTS = {
    ChatEntity.CASE: [
        "core.cases.get_case",
        "core.cases.list_cases",
        "core.cases.update_case",
        "core.cases.create_comment",
        "core.cases.list_comments",
    ],
    ChatEntity.AGENT_PRESET: [],
    ChatEntity.AGENT_PRESET_BUILDER: AGENT_PRESET_BUILDER_TOOL_NAMES,
    ChatEntity.COPILOT: [
        "core.table.list_tables",
        "core.table.get_table_metadata",
        "core.table.lookup",
        "core.table.search_rows",
        "core.cases.list_cases",
        "core.cases.get_case",
        "core.cases.search_cases",
    ],
}


def get_default_tools(entity_type: str) -> list[str]:
    """Get default tools for an entity type."""
    return TOOL_DEFAULTS.get(ChatEntity(entity_type), [])
