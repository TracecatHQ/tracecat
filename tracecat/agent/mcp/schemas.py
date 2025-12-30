"""Schema fetching utilities for MCP tool definitions."""

from __future__ import annotations

from tracecat.agent.mcp.types import MCPToolDefinition
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService


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
        for action_name in action_names:
            try:
                ra = await svc.get_action(action_name)
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
                    "Failed to fetch tool definition",
                    action_name=action_name,
                    error=str(e),
                )

    logger.info(
        "Fetched tool definitions",
        count=len(definitions),
        action_names=list(definitions.keys()),
    )

    return definitions
