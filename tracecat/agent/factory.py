from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import Agent, ModelSettings, Tool
from pydantic_ai.agent import AbstractAgent
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.tools import DeferredToolRequests

from tracecat.agent.context import (
    trim_history_processor,
    truncate_tool_returns_processor,
)
from tracecat.agent.parsers import parse_output_type
from tracecat.agent.prompts import ToolCallPrompt, VerbosityPrompt
from tracecat.agent.providers import get_model
from tracecat.agent.tools import build_agent_tools
from tracecat.agent.types import AgentConfig

type AgentFactory = Callable[[AgentConfig], Awaitable[AbstractAgent[Any, Any]]]


async def build_agent(config: AgentConfig) -> Agent[Any, Any]:
    """The default factory for building an agent."""

    agent_tools: list[Tool[Any | None]] = []
    tool_prompt_tools: list[Tool[Any | None]] = []
    if config.actions:
        tools = await build_agent_tools(
            namespaces=config.namespaces,
            actions=config.actions,
            tool_approvals=config.tool_approvals,
        )
        agent_tools.extend(tools.tools)
        tool_prompt_tools.extend(tools.tools)
    if config.custom_tools:
        agent_tools.extend(config.custom_tools)
        tool_prompt_tools.extend(config.custom_tools)
    _output_type = parse_output_type(config.output_type)
    _model_settings = (
        ModelSettings(**config.model_settings) if config.model_settings else None
    )

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
        toolsets = [
            MCPServerStreamableHTTP(
                url=server["url"],
                headers=server["headers"],
            )
            for server in config.mcp_servers
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
        history_processors=[truncate_tool_returns_processor, trim_history_processor],
    )
    return agent
