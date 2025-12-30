"""MCP utility functions for tool name normalization."""

from __future__ import annotations


def _tool_name_to_action_name(tool_name: str) -> str:
    """Convert MCP tool name (underscores) back to action name (dots).

    Example: tools__slack__post_message -> tools.slack.post_message
    """
    return tool_name.replace("__", ".")


def normalize_mcp_tool_name(mcp_tool_name: str) -> str:
    """Convert MCP tool name to readable action name for display.

    MCP tool naming convention: mcp__{server_name}__{tool_name}

    Handles both user MCP tools and Tracecat actions:
    - mcp__tracecat-proxy__tools__slack__post_message -> tools.slack.post_message
    - mcp__tracecat-actions__tools__slack__post_message -> tools.slack.post_message
    - mcp__user-mcp-server__some_tool -> some_tool (unchanged after prefix removal)
    - tools__slack__post_message -> tools.slack.post_message

    Args:
        mcp_tool_name: The MCP tool name to normalize

    Returns:
        Human-readable action/tool name
    """
    # Handle full MCP tool names with Tracecat server prefixes
    if mcp_tool_name.startswith("mcp__tracecat-proxy__"):
        tool_part = mcp_tool_name.replace("mcp__tracecat-proxy__", "")
        return _tool_name_to_action_name(tool_part)
    # Return as-is for other tool names
    return mcp_tool_name
