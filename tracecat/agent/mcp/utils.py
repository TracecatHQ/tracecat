"""MCP utility functions for tool name normalization and definition fetching.

This module provides pure utility functions for MCP tool name conversion
that can be imported without pulling in heavy dependencies (DB, logging).

The fetch_tool_definitions() function requires DB access and uses lazy imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition

if TYPE_CHECKING:
    from tracecat.agent.common.types import MCPServerConfig

REGISTRY_MCP_SERVER_NAME = "tracecat-registry"


def is_http_server(config: MCPServerConfig) -> TypeGuard[MCPHttpServerConfig]:
    """Return True if the MCP server config is an HTTP/SSE server.

    Legacy HTTP configs may omit "type"; treat missing as HTTP for compatibility.
    """
    return config.get("type", "http") == "http"


LEGACY_REGISTRY_MCP_SERVER_NAME = "tracecat_registry"


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


def _resolve_canonical_user_mcp_name(
    canonical_tool_name: str,
    *,
    known_server_names: set[str] | None = None,
) -> str:
    """Preserve full configured MCP server names when they contain dots."""
    if not canonical_tool_name.startswith("mcp.") or not known_server_names:
        return canonical_tool_name

    canonical_name = canonical_tool_name.removeprefix("mcp.")
    for known_server_name in sorted(known_server_names, key=len, reverse=True):
        if canonical_name.startswith(f"{known_server_name}."):
            return canonical_tool_name

    return canonical_tool_name


def normalize_mcp_tool_name(
    mcp_tool_name: str,
    *,
    known_server_names: set[str] | None = None,
) -> str:
    """Convert MCP tool name to readable action name for display.

    MCP tool naming convention: mcp__{server_name}__{tool_name}

    Handles Tracecat registry tools:
    - mcp__tracecat-registry__tools__slack__post_message -> tools.slack.post_message
    - mcp__tracecat_registry__tools__slack__post_message -> tools.slack.post_message
    - mcp.tracecat-registry.core.cases.create_case -> core.cases.create_case
    - mcp.tracecat_registry.core.cases.create_case -> core.cases.create_case

    Handles user MCP servers routed through the proxy:
    - mcp__tracecat-registry__mcp__Linear__list_issues -> mcp.Linear.list_issues
    - mcp__tracecat_registry__mcp__Linear__list_issues -> mcp.Linear.list_issues
    - mcp.tracecat-registry.mcp.Linear.list_issues -> mcp.Linear.list_issues
    - mcp.tracecat_registry.mcp.Linear.list_issues -> mcp.Linear.list_issues

    Other canonical user MCP names are returned as-is.

    Args:
        mcp_tool_name: The MCP tool name to normalize

    Returns:
        Human-readable action/tool name
    """
    # Handle user MCP tools routed through proxy (dot-separated, persisted)
    # Pattern: mcp.tracecat-registry.mcp.{server}.{tool}
    if mcp_tool_name.startswith(
        f"mcp.{REGISTRY_MCP_SERVER_NAME}.mcp."
    ) or mcp_tool_name.startswith(f"mcp.{LEGACY_REGISTRY_MCP_SERVER_NAME}.mcp."):
        # Extract mcp.{server}.{tool} part
        canonical_name = mcp_tool_name.replace(
            f"mcp.{REGISTRY_MCP_SERVER_NAME}.", "", 1
        ).replace(f"mcp.{LEGACY_REGISTRY_MCP_SERVER_NAME}.", "", 1)
        return _resolve_canonical_user_mcp_name(
            canonical_name,
            known_server_names=known_server_names,
        )

    # Handle user MCP tools routed through proxy (underscore-separated, runtime)
    # Pattern: mcp__tracecat-registry__mcp__{server}__{tool}
    if mcp_tool_name.startswith(
        f"mcp__{REGISTRY_MCP_SERVER_NAME}__mcp__"
    ) or mcp_tool_name.startswith(f"mcp__{LEGACY_REGISTRY_MCP_SERVER_NAME}__mcp__"):
        # Extract mcp__{server}__{tool} part and convert to mcp.{server}.{tool}
        tool_part = mcp_tool_name.replace(
            f"mcp__{REGISTRY_MCP_SERVER_NAME}__", ""
        ).replace(f"mcp__{LEGACY_REGISTRY_MCP_SERVER_NAME}__", "")
        canonical_name = mcp_tool_name_to_action_name(tool_part)
        return _resolve_canonical_user_mcp_name(
            canonical_name,
            known_server_names=known_server_names,
        )

    # Handle dot-separated format (persisted messages) for registry tools
    if mcp_tool_name.startswith(
        f"mcp.{REGISTRY_MCP_SERVER_NAME}."
    ) or mcp_tool_name.startswith(f"mcp.{LEGACY_REGISTRY_MCP_SERVER_NAME}."):
        return mcp_tool_name.replace(f"mcp.{REGISTRY_MCP_SERVER_NAME}.", "", 1).replace(
            f"mcp.{LEGACY_REGISTRY_MCP_SERVER_NAME}.", "", 1
        )

    # Handle underscore-separated format (runtime MCP tool names) for registry tools
    if mcp_tool_name.startswith(
        f"mcp__{REGISTRY_MCP_SERVER_NAME}__"
    ) or mcp_tool_name.startswith(f"mcp__{LEGACY_REGISTRY_MCP_SERVER_NAME}__"):
        tool_part = mcp_tool_name.replace(
            f"mcp__{REGISTRY_MCP_SERVER_NAME}__", ""
        ).replace(f"mcp__{LEGACY_REGISTRY_MCP_SERVER_NAME}__", "")
        return mcp_tool_name_to_action_name(tool_part)

    # Canonical user MCP names are already normalized. Keep the server segment
    # so approval/history flows remain unambiguous across integrations.
    if mcp_tool_name.startswith("mcp."):
        return _resolve_canonical_user_mcp_name(
            mcp_tool_name,
            known_server_names=known_server_names,
        )

    # Handle pattern: mcp__{server-name}__{tool_name}
    if mcp_tool_name.startswith("mcp__"):
        parts = mcp_tool_name.split("__", 2)
        if len(parts) >= 3:
            # Return user MCP tool as canonical form: mcp.{server}.{tool_name}
            return f"mcp.{parts[1]}.{mcp_tool_name_to_action_name(parts[2])}"

    # Handle runtime registry/internal tool names (e.g. core__http_request)
    if "__" in mcp_tool_name:
        return mcp_tool_name_to_action_name(mcp_tool_name)

    # Other tool names returned as-is
    return mcp_tool_name


def mcp_tool_name_to_canonical(discovered_name: str) -> str:
    """Convert discovered MCP tool name to canonical dot format.

    Discovered names follow the pattern ``mcp__{server}__{tool}``.
    The canonical format is ``mcp.{server}.{tool}``.

    This must produce the same result as :func:`normalize_mcp_tool_name`
    when given the runtime-wrapped version
    ``mcp__tracecat-registry__mcp__{server}__{tool}``.

    Examples:
        mcp__Linear__list_issues  -> mcp.Linear.list_issues
        mcp__Sentry__get_issue    -> mcp.Sentry.get_issue
    """
    if discovered_name.startswith("mcp__"):
        parts = discovered_name.split("__", 2)
        if len(parts) >= 3:
            return f"mcp.{parts[1]}.{mcp_tool_name_to_action_name(parts[2])}"
    return discovered_name


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
