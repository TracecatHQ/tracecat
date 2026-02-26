from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeGuard

from pydantic_ai import Agent, ModelSettings
from pydantic_ai.agent import AbstractAgent
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.tools import Tool as PATool

from tracecat.agent.common.types import MCPServerConfig, MCPUrlServerConfig
from tracecat.agent.parsers import parse_output_type
from tracecat.agent.prompts import ToolCallPrompt, VerbosityPrompt
from tracecat.agent.providers import get_model
from tracecat.agent.runtime.pydantic_ai.adapter import to_pydantic_ai_tools
from tracecat.agent.tools import build_agent_tools
from tracecat.agent.types import AgentConfig

type AgentFactory = Callable[[AgentConfig], Awaitable[AbstractAgent[Any, Any]]]


def _is_url_server(config: MCPServerConfig) -> TypeGuard[MCPUrlServerConfig]:
    return config["type"] == "url"


async def build_agent(config: AgentConfig) -> Agent[Any, Any]:
    """The default factory for building an agent."""

    agent_tools: list[PATool] = []
    tool_prompt_tools: list[PATool] = []
    if config.actions:
        tools_result = await build_agent_tools(
            namespaces=config.namespaces,
            actions=config.actions,
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

    toolsets = None
    if config.mcp_servers:
        url_servers = [
            server for server in config.mcp_servers if _is_url_server(server)
        ]
        toolsets = [
            MCPServerStreamableHTTP(
                url=server["url"],
                headers=server.get("headers", {}),
            )
            for server in url_servers
        ]

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
