"""Agent execution proxies for registry actions."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel

from tracecat_registry import ActionIsInterfaceError
from tracecat_registry import config


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
    """Configuration for a user-defined MCP server."""

    name: str
    """Required: Unique identifier for the server."""

    url: str
    """Required: HTTP/SSE endpoint URL for the MCP server."""

    headers: NotRequired[dict[str, str]]
    """Optional: Auth headers."""

    transport: NotRequired[Literal["http", "sse"]]
    """Optional: Transport type. Defaults to 'http'."""


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


async def build_agent(config: AgentConfig) -> object:
    """The default factory for building an agent.

    NOTE: This function cannot be executed in sandbox mode.
    It is an interface definition only.
    """
    raise ActionIsInterfaceError()


async def run_agent_sync(
    agent: object,
    user_prompt: str,
    max_requests: int,
    max_tools_calls: int | None = None,
    *,
    deferred_tool_results: object | None = None,
) -> AgentOutput:
    """Run an agent synchronously.

    NOTE: This function cannot be executed in sandbox mode.
    It is an interface definition only.
    """
    raise ActionIsInterfaceError()


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
    """Run an AI agent with specified configuration and actions.

    NOTE: This function cannot be executed in sandbox mode.
    It is an interface definition only.
    """
    raise ActionIsInterfaceError()


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
    """Rank items using an LLM based on natural language criteria.

    NOTE: This function cannot be executed in sandbox mode.
    It is an interface definition only.
    """
    raise ActionIsInterfaceError()


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
    """Rank items using LLM pairwise comparisons.

    NOTE: This function cannot be executed in sandbox mode.
    It is an interface definition only.
    """
    raise ActionIsInterfaceError()


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
