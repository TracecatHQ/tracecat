"""MCP utility functions for tool name normalization and definition fetching."""

from __future__ import annotations

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService


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


async def fetch_tool_definitions(
    action_names: list[str],
) -> dict[str, MCPToolDefinition]:
    """Fetch tool definitions from registry for given action names.

    Called by the job creator (who has DB access) before launching proxy.
    Returns dict mapping action names to their full definitions.

    Args:
        action_names: List of action names to fetch definitions for.

    Returns:
        Dict mapping action names to MCPToolDefinition objects.
    """
    definitions: dict[str, MCPToolDefinition] = {}

    async with RegistryActionsService.with_session() as svc:
        # Batch fetch all actions in a single query
        registry_actions = await svc.get_actions(action_names)

        # Build a lookup map by full action name
        action_map = {f"{ra.namespace}.{ra.name}": ra for ra in registry_actions}

        for action_name in action_names:
            try:
                ra = action_map.get(action_name)
                if ra is None:
                    logger.warning(
                        "Action not found in registry",
                        action_name=action_name,
                    )
                    continue

                bound = svc.get_bound(ra, mode="execution")

                # Extract JSON schema based on action type
                if bound.is_template and bound.template_action:
                    expects = bound.template_action.definition.expects
                    model_cls = create_expectation_model(
                        expects,
                        action_name.replace(".", "__"),
                    )
                    json_schema = model_cls.model_json_schema()
                elif bound.args_cls:
                    json_schema = bound.args_cls.model_json_schema()
                else:
                    json_schema = {"type": "object", "properties": {}}

                definitions[action_name] = MCPToolDefinition(
                    name=action_name,
                    description=bound.description or f"Execute {action_name}",
                    parameters_json_schema=json_schema,
                )

                logger.debug(
                    "Fetched tool definition",
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
