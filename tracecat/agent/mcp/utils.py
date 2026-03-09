"""MCP utility functions for canonical tool naming and definition fetching.

This module owns the conversion between Tracecat's canonical internal tool names
and the wire names used by Claude/MCP.

Canonical internal names:
- Registry tools: ``core.http_request``
- Internal tools: ``internal.builder.get_preset_summary``
- User MCP tools: ``mcp.Linear.list_issues``

Wire/legacy names are decoded at the edges and should not leak deeper into
Tracecat-owned state.

The fetch_tool_definitions() function requires DB access and uses lazy imports.
"""

from __future__ import annotations

from tracecat.agent.common.types import MCPToolDefinition

REGISTRY_MCP_SERVER_NAMES = frozenset({"tracecat-registry", "tracecat_registry"})
TRACECAT_ACTION_NAMESPACE_PREFIXES = frozenset({"core", "internal", "tools"})


def is_reserved_mcp_server_name(server_name: str) -> bool:
    """Return whether a server name is reserved for Tracecat's registry proxy."""
    return server_name in REGISTRY_MCP_SERVER_NAMES


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


def canonical_user_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build the canonical internal name for a user MCP tool."""
    return f"mcp.{server_name}.{tool_name}"


def parse_canonical_user_mcp_tool_name(
    tool_name: str,
) -> tuple[str, str] | None:
    """Parse a canonical user MCP tool name into ``(server_name, tool_name)``."""
    if not tool_name.startswith("mcp."):
        return None

    parts = tool_name.split(".", 2)
    if len(parts) < 3 or is_reserved_mcp_server_name(parts[1]):
        return None
    return (parts[1], parts[2])


def encode_canonical_tool_name_to_sdk(tool_name: str) -> str:
    """Encode a canonical tool name into the MCP tool name exposed to Claude."""
    if parsed := parse_canonical_user_mcp_tool_name(tool_name):
        server_name, original_tool_name = parsed
        return f"mcp__{server_name}__{original_tool_name}"
    return action_name_to_mcp_tool_name(tool_name)


def is_tracecat_sdk_tool_name(raw_tool_name: str) -> bool:
    """Return whether a raw SDK tool name uses Tracecat's action encoding."""
    prefix, _, _ = raw_tool_name.partition("__")
    return prefix in TRACECAT_ACTION_NAMESPACE_PREFIXES


def decode_sdk_tool_name_to_canonical(raw_tool_name: str) -> str:
    """Decode a raw SDK/MCP tool name into Tracecat's canonical form."""
    if parsed := parse_canonical_user_mcp_tool_name(raw_tool_name):
        return canonical_user_mcp_tool_name(*parsed)

    if raw_tool_name.startswith("mcp__"):
        parts = raw_tool_name.split("__", 2)
        if len(parts) >= 3:
            server_name, server_tool_name = parts[1], parts[2]
            if is_reserved_mcp_server_name(server_name):
                return decode_sdk_tool_name_to_canonical(server_tool_name)
            return canonical_user_mcp_tool_name(server_name, server_tool_name)

    if "__" in raw_tool_name and is_tracecat_sdk_tool_name(raw_tool_name):
        return mcp_tool_name_to_action_name(raw_tool_name)

    return raw_tool_name


def decode_legacy_tool_name_to_canonical(tool_name: str) -> str:
    """Decode legacy persisted/wire tool names into Tracecat's canonical form."""
    if any(
        tool_name.startswith(f"mcp.{alias}.") for alias in REGISTRY_MCP_SERVER_NAMES
    ):
        _, _, remainder = tool_name.split(".", 2)
        return decode_sdk_tool_name_to_canonical(remainder)

    return decode_sdk_tool_name_to_canonical(tool_name)


def normalize_mcp_tool_name(mcp_tool_name: str) -> str:
    """Backward-compatible wrapper for legacy call sites.

    Prefer ``decode_sdk_tool_name_to_canonical()`` or
    ``decode_legacy_tool_name_to_canonical()`` at new call sites.
    """
    return decode_legacy_tool_name_to_canonical(mcp_tool_name)


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
