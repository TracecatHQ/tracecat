"""Agent execution proxies for registry actions."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Literal, TypedDict, cast

from pydantic import BaseModel

from tracecat_registry import ActionIsInterfaceError, config


type OutputType = (
    Literal[
        "bool",
        "float",
        "int",
        "str",
        "list[bool]",
        "list[float]",
        "list[int]",
        "list[str]",
    ]
    | dict[str, object]
)


class MCPServerConfig(TypedDict):
    """Configuration for an MCP server."""

    url: str
    headers: dict[str, str]


class RankableItem(TypedDict):
    id: str | int
    text: str


@dataclass(kw_only=True, slots=True)
class AgentConfig:
    """Configuration for an agent."""

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
    tool_approvals: dict[str, bool] | None = None
    # MCP
    model_settings: dict[str, object] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    retries: int = config.TRACECAT__AGENT_MAX_RETRIES
    deps_type: type[object] | None = None
    custom_tools: list[object] | None = None


class AgentOutput(BaseModel):
    output: object
    message_history: list[object] | None = None
    duration: float
    usage: object
    session_id: uuid.UUID


def _raise_registry_client() -> None:
    if config.flags.registry_client:
        raise ActionIsInterfaceError()


async def build_agent(config: AgentConfig) -> object:
    """The default factory for building an agent."""
    _raise_registry_client()
    from tracecat.agent.factory import build_agent as _build_agent
    from tracecat.agent.types import AgentConfig as RuntimeAgentConfig

    from tracecat.agent.types import CustomToolList

    runtime_config = RuntimeAgentConfig(
        model_name=config.model_name,
        model_provider=config.model_provider,
        base_url=config.base_url,
        instructions=config.instructions,
        output_type=config.output_type,
        actions=config.actions,
        namespaces=config.namespaces,
        tool_approvals=config.tool_approvals,
        model_settings=config.model_settings,
        mcp_servers=config.mcp_servers,
        retries=config.retries,
        deps_type=config.deps_type,
        custom_tools=cast(CustomToolList | None, config.custom_tools),
    )
    return await _build_agent(runtime_config)


async def run_agent_sync(
    agent: object,
    user_prompt: str,
    max_requests: int,
    max_tools_calls: int | None = None,
    *,
    deferred_tool_results: object | None = None,
) -> AgentOutput:
    """Run an agent synchronously."""
    _raise_registry_client()
    from pydantic_ai import Agent as PydanticAgent
    from pydantic_ai.tools import DeferredToolResults
    from tracecat.agent.runtime.pydantic_ai.runtime import (
        run_agent_sync as _run_agent_sync,
    )

    result = await _run_agent_sync(
        cast(PydanticAgent[object, object], agent),
        user_prompt,
        max_requests,
        max_tools_calls,
        deferred_tool_results=cast(DeferredToolResults | None, deferred_tool_results),
    )
    return AgentOutput.model_validate(result.model_dump())


async def run_agent(
    user_prompt: str,
    model_name: str,
    model_provider: str,
    actions: list[str] | None = None,
    namespaces: list[str] | None = None,
    tool_approvals: dict[str, bool] | None = None,
    mcp_server_url: str | None = None,
    mcp_server_headers: dict[str, str] | None = None,
    mcp_servers: list[MCPServerConfig] | None = None,
    instructions: str | None = None,
    output_type: OutputType | None = None,
    model_settings: dict[str, object] | None = None,
    max_tool_calls: int = config.TRACECAT__AGENT_MAX_TOOL_CALLS,
    max_requests: int = config.TRACECAT__AGENT_MAX_REQUESTS,
    retries: int = config.TRACECAT__AGENT_MAX_RETRIES,
    base_url: str | None = None,
    deferred_tool_results: object | None = None,
) -> AgentOutput:
    """Run an AI agent with specified configuration and actions."""
    _raise_registry_client()
    from pydantic_ai.tools import DeferredToolResults
    from tracecat.agent.runtime.pydantic_ai.runtime import run_agent as _run_agent

    result = await _run_agent(
        user_prompt=user_prompt,
        model_name=model_name,
        model_provider=model_provider,
        actions=actions,
        namespaces=namespaces,
        tool_approvals=tool_approvals,
        mcp_server_url=mcp_server_url,
        mcp_server_headers=mcp_server_headers,
        mcp_servers=mcp_servers,
        instructions=instructions,
        output_type=output_type,
        model_settings=model_settings,
        max_tool_calls=max_tool_calls,
        max_requests=max_requests,
        retries=retries,
        base_url=base_url,
        deferred_tool_results=cast(DeferredToolResults | None, deferred_tool_results),
    )
    return AgentOutput.model_validate(result.model_dump())


async def rank_items(
    items: list[RankableItem],
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
    model_settings: dict[str, object] | None = None,
    max_requests: int = 5,
    retries: int = 3,
    base_url: str | None = None,
    *,
    min_items: int | None = None,
    max_items: int | None = None,
) -> list[str | int]:
    """Rank items using an LLM based on natural language criteria."""
    _raise_registry_client()
    from tracecat.ai.ranker import rank_items as _rank_items

    return await _rank_items(
        items=items,
        criteria_prompt=criteria_prompt,
        model_name=model_name,
        model_provider=model_provider,
        model_settings=model_settings,
        max_requests=max_requests,
        retries=retries,
        base_url=base_url,
        min_items=min_items,
        max_items=max_items,
    )


async def rank_items_pairwise(
    items: list[RankableItem],
    criteria_prompt: str,
    model_name: str,
    model_provider: str,
    id_field: str = "id",
    batch_size: int = 10,
    num_passes: int = 10,
    refinement_ratio: float = 0.5,
    model_settings: dict[str, object] | None = None,
    max_requests: int = 5,
    retries: int = 3,
    base_url: str | None = None,
    *,
    min_items: int | None = None,
    max_items: int | None = None,
) -> list[str | int]:
    """Rank items using LLM pairwise comparisons."""
    _raise_registry_client()
    from tracecat.ai.ranker import rank_items_pairwise as _rank_items_pairwise

    return await _rank_items_pairwise(
        items=items,
        criteria_prompt=criteria_prompt,
        model_name=model_name,
        model_provider=model_provider,
        id_field=id_field,
        batch_size=batch_size,
        num_passes=num_passes,
        refinement_ratio=refinement_ratio,
        model_settings=model_settings,
        max_requests=max_requests,
        retries=retries,
        base_url=base_url,
        min_items=min_items,
        max_items=max_items,
    )


__all__ = [
    "AgentConfig",
    "AgentOutput",
    "MCPServerConfig",
    "OutputType",
    "RankableItem",
    "build_agent",
    "rank_items",
    "rank_items_pairwise",
    "run_agent",
    "run_agent_sync",
]
