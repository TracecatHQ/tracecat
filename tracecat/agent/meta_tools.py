"""Meta-tools MCP server for dynamic registry access.

Provides 3 tools that give the agent access to all 400+ registry actions:
- list_tools: Discover available tools with filtering
- get_tool_schema: Get input schema for a specific tool
- execute_tool: Execute any tool by name
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from tracecat.agent.tools import call_tracecat_action
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService


@tool(
    "list_tools",
    "List available tools in the registry. Use namespace to filter by prefix (e.g., 'tools.slack', 'core'). Use search to filter by name/description.",
    {"namespace": str, "search": str},
)
async def list_tools(args: dict[str, Any]) -> dict[str, Any]:
    """List available tools with optional filtering."""
    namespace = args.get("namespace")
    search = args.get("search", "").lower() if args.get("search") else None

    try:
        async with RegistryActionsService.with_session() as svc:
            actions = await svc.list_actions(namespace=namespace)

        results = []
        for ra in actions:
            name = f"{ra.namespace}.{ra.name}"
            desc = ra.description or ""

            # Apply search filter
            if search and search not in (name + desc).lower():
                continue

            results.append({"name": name, "description": desc})

        # Sort by name for consistency
        results.sort(key=lambda x: x["name"])

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Found {len(results)} tools:\n{json.dumps(results, indent=2)}",
                }
            ]
        }
    except Exception as e:
        logger.error("list_tools failed", error=str(e))
        return {"content": [{"type": "text", "text": f"Error listing tools: {e!s}"}]}


@tool(
    "get_tool_schema",
    "Get the input schema (parameters) for a specific tool. Call this before execute_tool to understand required arguments.",
    {"tool_name": str},
)
async def get_tool_schema(args: dict[str, Any]) -> dict[str, Any]:
    """Get the JSON schema for a tool's input parameters."""
    tool_name = args["tool_name"]

    try:
        async with RegistryActionsService.with_session() as svc:
            ra = await svc.get_action(tool_name)
            bound = svc.get_bound(ra, mode="execution")

            # Get schema based on action type
            if bound.is_template and bound.template_action:
                # For templates, build schema from expects
                from tracecat.expressions.expectations import create_expectation_model

                expects = bound.template_action.definition.expects
                model_cls = create_expectation_model(
                    expects, bound.template_action.definition.action.replace(".", "__")
                )
                schema = model_cls.model_json_schema()
            elif bound.args_cls:
                schema = bound.args_cls.model_json_schema()
            else:
                schema = {"type": "object", "properties": {}}

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Schema for {tool_name}:\n{json.dumps(schema, indent=2)}",
                }
            ]
        }
    except Exception as e:
        logger.error("get_tool_schema failed", tool_name=tool_name, error=str(e))
        return {
            "content": [
                {"type": "text", "text": f"Error getting schema for {tool_name}: {e!s}"}
            ]
        }


@tool(
    "execute_tool",
    "Execute any registry tool by name with the provided arguments. Use get_tool_schema first to understand required parameters.",
    {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Full tool name (e.g., 'tools.slack.post_message', 'core.http_request')",
            },
            "args": {
                "type": "object",
                "description": "Arguments to pass to the tool (must match the tool's schema)",
            },
        },
        "required": ["tool_name", "args"],
    },
)
async def execute_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Execute a registry tool by name."""
    tool_name = args["tool_name"]
    tool_args = args.get("args", {})

    logger.info("Executing tool via meta-tool", tool_name=tool_name)

    try:
        result = await call_tracecat_action(tool_name, tool_args)

        # Format result as text
        if isinstance(result, (dict, list)):
            result_text = json.dumps(result, indent=2, default=str)
        else:
            result_text = str(result)

        return {
            "content": [
                {"type": "text", "text": f"Tool {tool_name} result:\n{result_text}"}
            ]
        }
    except Exception as e:
        logger.error("execute_tool failed", tool_name=tool_name, error=str(e))
        return {
            "content": [{"type": "text", "text": f"Error executing {tool_name}: {e!s}"}]
        }


def create_registry_mcp_server():
    """Create the MCP server with registry meta-tools."""
    return create_sdk_mcp_server(
        name="tracecat-registry",
        version="1.0.0",
        tools=[list_tools, get_tool_schema, execute_tool],
    )
