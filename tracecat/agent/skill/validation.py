"""Shared support checks for declared skill capabilities."""

from tracecat.agent.skill.schemas import SkillValidationErrorDetail

STDIO_MCP_TOOL_SUBSET_UNSUPPORTED = "stdio_mcp_tool_subset_unsupported"


def get_mcp_grant_support_error(
    *, server_type: str, tool_name: str | None, tool_id: str
) -> SkillValidationErrorDetail | None:
    """Reject subset grants until the stdio transport can enforce them."""
    if server_type != "stdio" or tool_name is None:
        return None
    return SkillValidationErrorDetail(
        code=STDIO_MCP_TOOL_SUBSET_UNSUPPORTED,
        message=(
            f"Cannot add '{tool_id}' on its own. Stdio MCP servers currently "
            "require access to all their tools. Add the whole server instead, "
            "or use an HTTP MCP server to select individual tools."
        ),
        path="SKILL.md",
    )
