from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import Agent, ModelSettings, StructuredDict, Tool
from pydantic_ai.agent import AbstractAgent
from pydantic_ai.mcp import MCPServerStreamableHTTP

from tracecat.agent.models import AgentConfig, OutputType
from tracecat.agent.prompts import ToolCallPrompt, VerbosityPrompt
from tracecat.agent.providers import get_model
from tracecat.agent.tools import build_agent_tools

type AgentFactory = Callable[[AgentConfig], Awaitable[AbstractAgent[Any, Any]]]


SUPPORTED_OUTPUT_TYPES: dict[str, type[Any]] = {
    "bool": bool,
    "float": float,
    "int": int,
    "str": str,
    "list[bool]": list[bool],
    "list[float]": list[float],
    "list[int]": list[int],
    "list[str]": list[str],
}


def _parse_output_type(output_type: OutputType) -> type[Any]:
    if isinstance(output_type, str):
        try:
            return SUPPORTED_OUTPUT_TYPES[output_type]
        except KeyError as e:
            raise ValueError(
                f"Unknown output type: {output_type}. Expected one of: {', '.join(SUPPORTED_OUTPUT_TYPES.keys())}"
            ) from e
    elif isinstance(output_type, dict):
        schema_name = output_type.get("name") or output_type.get("title")
        schema_description = output_type.get("description")
        return StructuredDict(
            output_type, name=schema_name, description=schema_description
        )
    else:
        return str


async def build_agent(config: AgentConfig) -> Agent[Any, Any]:
    """The default factory for building an agent."""

    agent_tools: list[Tool[Any | None]] = []
    if config.actions:
        tools = await build_agent_tools(
            fixed_arguments=config.fixed_arguments,
            namespaces=config.namespaces,
            actions=config.actions,
        )
        agent_tools = tools.tools
    _output_type = _parse_output_type(config.output_type) if config.output_type else str
    _model_settings = (
        ModelSettings(**config.model_settings) if config.model_settings else None
    )

    # Add verbosity prompt
    verbosity_prompt = VerbosityPrompt()
    instructions = f"{config.instructions}\n{verbosity_prompt.prompt}"

    if config.actions:
        tool_calling_prompt = ToolCallPrompt(
            tools=tools.tools,
            fixed_arguments=config.fixed_arguments,
        )
        instruction_parts = [instructions, tool_calling_prompt.prompt]
        instructions = "\n".join(part for part in instruction_parts if part)

    toolsets = None
    if config.mcp_server_url:
        mcp_server = MCPServerStreamableHTTP(
            url=config.mcp_server_url,
            headers=config.mcp_server_headers,
        )
        toolsets = [mcp_server]

    model = get_model(config.model_name, config.model_provider, config.base_url)
    agent = Agent(
        model=model,
        instructions=instructions,
        output_type=_output_type,
        model_settings=_model_settings,
        retries=config.retries,
        instrument=True,
        tools=agent_tools,
        toolsets=toolsets,
        deps_type=config.deps_type or type(None),
    )
    return agent
