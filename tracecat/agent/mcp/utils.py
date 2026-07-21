"""MCP utility functions for tool name normalization and definition fetching.

This module provides pure utility functions for MCP tool name conversion
that can be imported without pulling in heavy dependencies (DB, logging).

The tool definition fetchers require DB access and use lazy imports.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from mcp.types import (
    AudioContent,
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from tracecat.agent.common.types import MCPToolDefinition

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tracecat.identifiers import OrganizationID
    from tracecat.registry.lock.types import RegistryLock

REGISTRY_MCP_SERVER_NAME = "tracecat-registry"
LEGACY_REGISTRY_MCP_SERVER_NAME = "tracecat_registry"

# Results cross the CLI stdout buffer capped at 5MiB
# (CLAUDE_SDK_MAX_BUFFER_SIZE_BYTES); stay under it with headroom for framing.
MCP_TOOL_RESULT_MAX_BYTES = 4 * 1024 * 1024


def _format_size(num_bytes: int) -> str:
    """Human-readable byte size."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}GB"


def _render_content_block(block: ContentBlock) -> str:
    """Render a single MCP content block as text.

    EmbeddedResource has no `.text`; the payload is nested on `.resource`.
    """
    if isinstance(block, TextContent):
        return block.text
    if isinstance(block, EmbeddedResource):
        resource = block.resource
        if isinstance(resource, TextResourceContents):
            return resource.text
        if isinstance(resource, BlobResourceContents):
            return (
                f"[binary resource: {resource.uri} ({resource.mimeType or 'unknown'})]"
            )
        return f"[resource: {resource.uri}]"
    if isinstance(block, ImageContent):
        return f"[image: {block.mimeType}]"
    if isinstance(block, AudioContent):
        return f"[audio: {block.mimeType}]"
    if isinstance(block, ResourceLink):
        return f"[resource link: {block.uri} ({block.mimeType or 'unknown'})]"
    return f"[unsupported content block: {type(block).__name__}]"


def flatten_mcp_content_blocks(
    content: Sequence[ContentBlock] | None,
    *,
    max_bytes: int = MCP_TOOL_RESULT_MAX_BYTES,
) -> str:
    """Flatten every block of an MCP CallToolResult into a single string.

    Truncation is marked loudly: a silent cut is the same failure mode as
    dropping blocks, since the agent cannot tell it received partial data.
    """
    if not content:
        return ""

    joined = "\n\n".join(_render_content_block(block) for block in content)

    encoded = joined.encode("utf-8")
    if len(encoded) <= max_bytes:
        return joined

    head = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return (
        f"{head}\n\n[truncated: {_format_size(len(encoded) - max_bytes)} "
        f"of {_format_size(len(encoded))} omitted]"
    )


# Anthropic tool names must match this pattern; stdio MCP servers can report
# names (e.g. "issue.get") that would put invalid entries in allowed_tools.
STDIO_MCP_TOOL_NAME_RE = re.compile(r"\A[a-zA-Z0-9_-]{1,64}\Z")


def action_name_to_mcp_tool_name(action_name: str) -> str:
    """Convert action name (dots) to MCP tool name format (underscores).

    Example: core.http_request -> core__http_request
    """
    return action_name.replace(".", "__")


def mcp_tool_name_to_action_name(tool_name: str) -> str:
    """Convert MCP tool name (underscores) back to action name (dots).

    Example: core__script__run_python -> core.script.run_python
    """
    return tool_name.replace("__", ".")


def normalize_mcp_tool_name(mcp_tool_name: str) -> str:
    """Convert MCP tool name to readable action name for display.

    MCP tool naming convention: mcp__{server_name}__{tool_name}

    Handles Tracecat registry tools:
    - mcp__tracecat-registry__core__http_request -> core.http_request
    - mcp__tracecat_registry__core__script__run_python -> core.script.run_python
    - mcp.tracecat-registry.core.http_request -> core.http_request
    - mcp.tracecat_registry.core.script.run_python -> core.script.run_python

    Handles user MCP servers routed through the proxy:
    - mcp__tracecat-registry__mcp__Linear__list_issues -> mcp.Linear.list_issues
    - mcp__tracecat_registry__mcp__Linear__list_issues -> mcp.Linear.list_issues
    - mcp.tracecat-registry.mcp.Linear.list_issues -> mcp.Linear.list_issues
    - mcp.tracecat_registry.mcp.Linear.list_issues -> mcp.Linear.list_issues

    Other MCP tool names are returned as-is.

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
        return mcp_tool_name.replace(f"mcp.{REGISTRY_MCP_SERVER_NAME}.", "", 1).replace(
            f"mcp.{LEGACY_REGISTRY_MCP_SERVER_NAME}.", "", 1
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
        return mcp_tool_name_to_action_name(tool_part)

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


async def fetch_tool_definitions_for_lock(
    action_names: list[str],
    registry_lock: RegistryLock,
    organization_id: OrganizationID,
) -> dict[str, MCPToolDefinition]:
    """Fetch tool definitions from the versions pinned in a registry lock."""
    from tracecat.executor import registry_resolver
    from tracecat.logger import logger

    definitions: dict[str, MCPToolDefinition] = {}
    await registry_resolver.prefetch_lock(registry_lock, organization_id)

    for action_name in action_names:
        try:
            manifest_action = await registry_resolver.resolve_manifest_action(
                action_name,
                registry_lock,
                organization_id,
            )
            definitions[action_name] = MCPToolDefinition(
                name=action_name,
                description=manifest_action.description or f"Execute {action_name}",
                parameters_json_schema=manifest_action.interface["expects"],
            )
            logger.debug(
                "Fetched tool definition from registry lock",
                action_name=action_name,
            )
        except Exception as e:
            logger.warning(
                "Failed to build locked tool definition",
                action_name=action_name,
                error=str(e),
            )

    logger.info(
        "Fetched tool definitions from registry lock",
        count=len(definitions),
        action_names=list(definitions.keys()),
    )
    return definitions
