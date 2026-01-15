"""MCP utility functions for tool name normalization.

This module provides pure utility functions for MCP tool name conversion
that can be imported without pulling in heavy dependencies (DB, logging).
"""

from __future__ import annotations


def action_name_to_mcp_tool_name(action_name: str) -> str:
    """Convert action name (dots) to MCP tool name format (underscores).

    Example: tools.slack.post_message -> tools__slack__post_message
    """
    return action_name.replace(".", "__")


def mcp_tool_name_to_action_name(tool_name: str) -> str:
    """Convert MCP tool name (underscores) back to action name (dots).

    Example: tools__slack__post_message -> tools.slack.post_message
    """
    return tool_name.replace("__", ".")


def normalize_mcp_tool_name(mcp_tool_name: str) -> str:
    """Convert MCP tool name to readable action name for display.

    MCP tool naming convention: mcp__{server_name}__{tool_name}

    Handles Tracecat proxy tools:
    - mcp__tracecat-registry__tools__slack__post_message -> tools.slack.post_message
    - mcp.tracecat-registry.core.cases.create_case -> core.cases.create_case

    Other MCP tool names are returned as-is.

    Args:
        mcp_tool_name: The MCP tool name to normalize

    Returns:
        Human-readable action/tool name
    """
    # Handle dot-separated format (persisted messages)
    if mcp_tool_name.startswith("mcp.tracecat-registry."):
        return mcp_tool_name.replace("mcp.tracecat-registry.", "", 1)

    # Handle underscore-separated format (runtime MCP tool names)
    if mcp_tool_name.startswith("mcp__tracecat-registry__"):
        tool_part = mcp_tool_name.replace("mcp__tracecat-registry__", "")
        return mcp_tool_name_to_action_name(tool_part)

    # Other MCP tool names returned as-is
    return mcp_tool_name
