from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from pydantic_ai import Agent, ModelSettings
from pydantic_ai.agent import AbstractAgent
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.tools import Tool as PATool

from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.mcp.utils import is_http_server
from tracecat.agent.parsers import parse_output_type
from tracecat.agent.prompts import ToolCallPrompt, VerbosityPrompt
from tracecat.agent.providers import get_model
from tracecat.agent.runtime.pydantic_ai.adapter import to_pydantic_ai_tools
from tracecat.agent.tools import build_agent_tools
from tracecat.agent.types import AgentConfig

type AgentFactory = Callable[[AgentConfig], Awaitable[AbstractAgent[Any, Any]]]


class _ToolsetWithGetTools(Protocol):
    async def get_tools(self, ctx: Any) -> dict[str, Any]: ...


class _FilteredMCPToolsetMixin:
    """Filter MCP tool exposure to an allowlisted subset when configured."""

    def __init__(
        self,
        *args: Any,
        allowed_tool_names: set[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._allowed_tool_names = (
            frozenset(allowed_tool_names) if allowed_tool_names else None
        )
        super().__init__(*args, **kwargs)

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        toolset = cast(_ToolsetWithGetTools, super())
        tools = await toolset.get_tools(ctx)
        if self._allowed_tool_names is None:
            return tools
        return {
            tool_name: tool
            for tool_name, tool in tools.items()
            if tool_name in self._allowed_tool_names
        }


class _FilteredMCPServerStreamableHTTP(
    _FilteredMCPToolsetMixin, MCPServerStreamableHTTP
):
    """HTTP MCP server with optional per-tool filtering."""


def _partition_actions(
    config: AgentConfig,
) -> tuple[list[str], dict[str, set[str]]]:
    """Split configured actions into registry actions and MCP tool allowlists."""
    registry_actions: list[str] = []
    selected_mcp_tools_by_server: dict[str, set[str]] = {}
    known_server_names = (
        {server["name"] for server in config.mcp_servers}
        if config.mcp_servers
        else None
    )

    for action_name in config.actions or []:
        if not (normalized_action := action_name.strip()):
            continue

        parsed = UserMCPClient.parse_user_mcp_tool_name(
            normalized_action,
            known_server_names=known_server_names,
        )
        if parsed is None:
            registry_actions.append(normalized_action)
            continue

        server_name, original_tool_name = parsed
        selected_mcp_tools_by_server.setdefault(server_name, set()).add(
            original_tool_name
        )

    return registry_actions, selected_mcp_tools_by_server


def _has_mcp_tool_approvals(config: AgentConfig) -> bool:
    """Return True when tool approvals target user MCP tools."""
    if not config.tool_approvals:
        return False

    known_server_names = (
        {server["name"] for server in config.mcp_servers}
        if config.mcp_servers
        else None
    )
    return any(
        UserMCPClient.parse_user_mcp_tool_name(
            tool_name,
            known_server_names=known_server_names,
        )
        is not None
        for tool_name in config.tool_approvals
    )


def _build_mcp_toolsets(
    config: AgentConfig,
    *,
    selected_mcp_tools_by_server: dict[str, set[str]],
) -> list[Any] | None:
    """Build MCP toolsets, optionally restricting each server to selected tools."""
    if not config.mcp_servers:
        return None

    toolsets: list[Any] = []
    selected_servers = (
        set(selected_mcp_tools_by_server) if selected_mcp_tools_by_server else None
    )
    seen_servers: set[str] = set()

    for server in config.mcp_servers:
        server_name = server["name"]
        if selected_servers is not None and server_name not in selected_servers:
            continue

        seen_servers.add(server_name)
        allowed_tool_names = selected_mcp_tools_by_server.get(server_name)

        if not is_http_server(server):
            if allowed_tool_names:
                raise ValueError(
                    "Stdio MCP servers are not supported in the PydanticAI runtime"
                )
            continue

        server_timeout = server.get("timeout")
        if server_timeout is None:
            toolsets.append(
                _FilteredMCPServerStreamableHTTP(
                    url=server["url"],
                    headers=server.get("headers", {}),
                    allowed_tool_names=allowed_tool_names,
                )
            )
        else:
            toolsets.append(
                _FilteredMCPServerStreamableHTTP(
                    url=server["url"],
                    headers=server.get("headers", {}),
                    timeout=float(server_timeout),
                    allowed_tool_names=allowed_tool_names,
                )
            )

    if selected_servers and (missing_servers := selected_servers - seen_servers):
        raise ValueError(
            "Requested MCP tools reference servers that are not configured: "
            f"{sorted(missing_servers)}"
        )

    return toolsets or None


async def build_agent(config: AgentConfig) -> Agent[Any, Any]:
    """The default factory for building an agent."""

    registry_actions, selected_mcp_tools_by_server = _partition_actions(config)
    if selected_mcp_tools_by_server and not config.mcp_servers:
        raise ValueError(
            "MCP tools were selected, but no MCP servers are configured: "
            f"{sorted(selected_mcp_tools_by_server)}"
        )

    if _has_mcp_tool_approvals(config):
        raise ValueError(
            "MCP tool approvals are not supported in the PydanticAI runtime"
        )

    agent_tools: list[PATool] = []
    tool_prompt_tools: list[PATool] = []
    if registry_actions:
        tools_result = await build_agent_tools(
            namespaces=config.namespaces,
            actions=registry_actions,
            tool_approvals=config.tool_approvals,
        )
        # Convert Tracecat Tools to pydantic-ai Tools
        pa_tools = to_pydantic_ai_tools(tools_result.tools)
        agent_tools.extend(pa_tools)
        tool_prompt_tools.extend(pa_tools)
    if config.custom_tools:
        agent_tools.extend(config.custom_tools)
        tool_prompt_tools.extend(config.custom_tools)
    _output_type = parse_output_type(config.output_type)
    # Disable parallel tool calls only if tools exist (OpenAI requires this)
    model_settings_dict = {**(config.model_settings or {})}
    if agent_tools or config.mcp_servers:
        model_settings_dict["parallel_tool_calls"] = False
    _model_settings = ModelSettings(**model_settings_dict)
    # Add verbosity prompt
    verbosity_prompt = VerbosityPrompt()
    instructions = f"{config.instructions}\n{verbosity_prompt.prompt}"

    if tool_prompt_tools:
        tool_calling_prompt = ToolCallPrompt(
            tools=tool_prompt_tools,
        )
        instruction_parts = [instructions, tool_calling_prompt.prompt]
        instructions = "\n".join(part for part in instruction_parts if part)

    toolsets = _build_mcp_toolsets(
        config,
        selected_mcp_tools_by_server=selected_mcp_tools_by_server,
    )

    output_type_for_agent: type[Any] | list[type[Any]]
    # If any tool requires approval, include DeferredToolRequests in output types
    if any(tool.requires_approval for tool in agent_tools):
        output_type_for_agent = [_output_type, DeferredToolRequests]
    else:
        output_type_for_agent = _output_type

    model = get_model(config.model_name, config.model_provider, config.base_url)
    agent = Agent(
        model=model,
        instructions=instructions,
        output_type=output_type_for_agent,
        model_settings=_model_settings,
        retries=config.retries,
        instrument=True,
        tools=agent_tools,
        toolsets=toolsets,
        deps_type=config.deps_type or type(None),
    )
    return agent
