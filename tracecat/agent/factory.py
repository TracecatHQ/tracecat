from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, ModelSettings, StructuredDict, Tool
from pydantic_ai.agent import AbstractAgent
from pydantic_ai.mcp import MCPServerStreamableHTTP

from tracecat.agent.models import OutputType
from tracecat.agent.prompts import ToolCallPrompt, VerbosityPrompt
from tracecat.agent.providers import get_model
from tracecat.agent.tools import build_agent_tools


@dataclass(kw_only=True, slots=True)
class BuildAgentArgs[DepsT]:
    # Model
    model_name: str
    model_provider: str
    base_url: str | None = None
    # Agent
    instructions: str | None = None
    output_type: OutputType | None = None
    # Tools
    actions: list[str] | None = None
    namespaces: list[str] | None = None
    fixed_arguments: dict[str, dict[str, Any]] | None = None
    # MCP
    mcp_server_url: str | None = None
    mcp_server_headers: dict[str, str] | None = None
    model_settings: dict[str, Any] | None = None
    retries: int = 3
    deps_type: type[DepsT] | None = None


type AgentFactory[DepsT] = Callable[
    [BuildAgentArgs[DepsT]], Awaitable[AbstractAgent[DepsT, Any]]
]


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


async def build_agent[DepsT](args: BuildAgentArgs[DepsT]) -> Agent[DepsT, Any]:
    agent_tools: list[Tool[DepsT | None]] = []
    if args.actions:
        tools = await build_agent_tools(
            fixed_arguments=args.fixed_arguments,
            namespaces=args.namespaces,
            actions=args.actions,
        )
        agent_tools = tools.tools
    _output_type = _parse_output_type(args.output_type) if args.output_type else str
    _model_settings = (
        ModelSettings(**args.model_settings) if args.model_settings else None
    )
    model = get_model(args.model_name, args.model_provider, args.base_url)

    # Add verbosity prompt
    verbosity_prompt = VerbosityPrompt()
    instructions = f"{args.instructions}\n{verbosity_prompt.prompt}"

    if args.actions:
        tool_calling_prompt = ToolCallPrompt(
            tools=tools.tools,
            fixed_arguments=args.fixed_arguments,
        )
        instruction_parts = [instructions, tool_calling_prompt.prompt]
        instructions = "\n".join(part for part in instruction_parts if part)

    toolsets = None
    if args.mcp_server_url:
        mcp_server = MCPServerStreamableHTTP(
            url=args.mcp_server_url,
            headers=args.mcp_server_headers,
        )
        toolsets = [mcp_server]

    agent = Agent(
        model=model,
        instructions=instructions,
        output_type=_output_type,
        model_settings=_model_settings,
        retries=args.retries,
        instrument=True,
        tools=agent_tools,
        toolsets=toolsets,
        deps_type=args.deps_type or type(None),
    )
    return agent
