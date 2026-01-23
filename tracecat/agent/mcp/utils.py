"""MCP utility functions for tool name normalization and definition fetching.

This module provides pure utility functions for MCP tool name conversion
that can be imported without pulling in heavy dependencies (DB, logging).

The fetch_tool_definitions() function requires DB access and uses lazy imports.
"""

from __future__ import annotations

from tracecat.agent.common.types import MCPToolDefinition


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

    Handles Tracecat registry tools:
    - mcp__tracecat-registry__tools__slack__post_message -> tools.slack.post_message
    - mcp.tracecat-registry.core.cases.create_case -> core.cases.create_case

    Handles user MCP servers routed through the proxy:
    - mcp__tracecat-registry__mcp__Linear__list_issues -> mcp.Linear.list_issues
    - mcp.tracecat-registry.mcp.Linear.list_issues -> mcp.Linear.list_issues

    Other MCP tool names are returned as-is.

    Args:
        mcp_tool_name: The MCP tool name to normalize

    Returns:
        Human-readable action/tool name
    """
    # Handle user MCP tools routed through proxy (dot-separated, persisted)
    # Pattern: mcp.tracecat-registry.mcp.{server}.{tool}
    if mcp_tool_name.startswith("mcp.tracecat-registry.mcp."):
        # Extract mcp.{server}.{tool} part
        return mcp_tool_name.replace("mcp.tracecat-registry.", "", 1)

    # Handle user MCP tools routed through proxy (underscore-separated, runtime)
    # Pattern: mcp__tracecat-registry__mcp__{server}__{tool}
    if mcp_tool_name.startswith("mcp__tracecat-registry__mcp__"):
        # Extract mcp__{server}__{tool} part and convert to mcp.{server}.{tool}
        tool_part = mcp_tool_name.replace("mcp__tracecat-registry__", "")
        return mcp_tool_name_to_action_name(tool_part)

    # Handle dot-separated format (persisted messages) for registry tools
    if mcp_tool_name.startswith("mcp.tracecat-registry."):
        return mcp_tool_name.replace("mcp.tracecat-registry.", "", 1)

    # Handle underscore-separated format (runtime MCP tool names) for registry tools
    if mcp_tool_name.startswith("mcp__tracecat-registry__"):
        tool_part = mcp_tool_name.replace("mcp__tracecat-registry__", "")
        return mcp_tool_name_to_action_name(tool_part)

    # Generic MCP prefix stripping for any other servers
    # Handle pattern: mcp.{server-name}.{tool_name}
    if mcp_tool_name.startswith("mcp."):
        parts = mcp_tool_name.split(".", 2)
        if len(parts) >= 3:
            # Return everything after mcp.{server-name}.
            return parts[2]

    # Handle pattern: mcp__{server-name}__{tool_name}
    if mcp_tool_name.startswith("mcp__"):
        parts = mcp_tool_name.split("__", 2)
        if len(parts) >= 3:
            # Return everything after mcp__{server-name}__ with underscores -> dots
            return mcp_tool_name_to_action_name(parts[2])

    # Other tool names returned as-is
    return mcp_tool_name


async def fetch_tool_definitions(
    action_names: list[str],
) -> dict[str, MCPToolDefinition]:
    """Fetch tool definitions from registry index/manifest for given action names.

    Called by the job creator (who has DB access) before launching proxy.
    Returns dict mapping action names to their full definitions.

    Note: This function uses lazy imports to avoid pulling in DB dependencies
    when only the pure utility functions are needed.

    Args:
        action_names: List of action names to fetch definitions for.

    Returns:
        Dict mapping action names to MCPToolDefinition objects.
    """
    # Lazy imports - only orchestrator calls this function
    from tracecat.agent.common.types import MCPToolDefinition
    from tracecat.logger import logger
    from tracecat.registry.actions.service import RegistryActionsService

    definitions: dict[str, MCPToolDefinition] = {}

    async with RegistryActionsService.with_session() as svc:
        # Batch fetch all actions from index/manifest
        actions_data = await svc.get_actions_from_index(action_names)

        for action_name in action_names:
            try:
                action_data = actions_data.get(action_name)
                if action_data is None:
                    logger.warning(
                        "Action not found in registry index",
                        action_name=action_name,
                    )
                    continue

                manifest_action = action_data.manifest.actions.get(action_name)
                if manifest_action is None:
                    logger.warning(
                        "Action not found in manifest",
                        action_name=action_name,
                    )
                    continue

                # Use interface from manifest
                json_schema = manifest_action.interface["expects"]

                definitions[action_name] = MCPToolDefinition(
                    name=action_name,
                    description=action_data.index_entry.description
                    or f"Execute {action_name}",
                    parameters_json_schema=json_schema,
                )

                logger.debug(
                    "Fetched tool definition from index/manifest",
                    action_name=action_name,
                )

            except Exception as e:
                logger.warning(
                    "Failed to build tool definition",
                    action_name=action_name,
                    error=str(e),
                )

    logger.info(
        "Fetched tool definitions",
        count=len(definitions),
        action_names=list(definitions.keys()),
    )

    return definitions
