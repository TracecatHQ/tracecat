"""Agent execution proxies and SDK client for registry actions."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel

from tracecat_registry import config
from tracecat_registry import types as registry_types
from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


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

    This function delegates to AgentsClient.run() via HTTP.

    Args:
        user_prompt: The main prompt/message for the agent.
        model_name: Name of the LLM model (e.g., "gpt-4", "claude-3").
        model_provider: Provider of the model (e.g., "openai", "anthropic").
        actions: List of action names to make available to the agent.
        namespaces: Optional list of namespaces to restrict available tools.
        tool_approvals: Optional per-tool approval requirements.
        mcp_server_url: (Legacy) Optional URL of the MCP server.
        mcp_server_headers: (Legacy) Optional headers for the MCP server.
        mcp_servers: Optional list of MCP server configurations.
        instructions: Optional system instructions for the agent.
        output_type: Optional specification for the agent's output format.
        model_settings: Optional model-specific configuration parameters.
        max_tool_calls: Maximum number of tool calls per agent run.
        max_requests: Maximum number of LLM requests per agent run.
        retries: Maximum number of retry attempts.
        base_url: Optional custom base URL for the model provider's API.
        deferred_tool_results: Results from deferred tool calls (for continuations).

    Returns:
        AgentOutput with result, message history, usage, and session ID.
    """
    from tracecat_registry.context import get_context

    # Handle legacy mcp_server_url/headers
    merged_mcp_servers: list[MCPServerConfig] | None = mcp_servers
    if mcp_server_url:
        if merged_mcp_servers is None:
            merged_mcp_servers = []
        merged_mcp_servers.append(
            MCPServerConfig(
                name="legacy",
                url=mcp_server_url,
                headers=mcp_server_headers or {},
            )
        )

    ctx = get_context()
    result = await ctx.agents.run(
        user_prompt=user_prompt,
        config=AgentConfig(
            model_name=model_name,
            model_provider=model_provider,
            actions=actions,
            namespaces=namespaces,
            tool_approvals=tool_approvals,
            mcp_servers=merged_mcp_servers,
            instructions=instructions,
            output_type=output_type,
            model_settings=model_settings,
            retries=retries,
            base_url=base_url,
        ),
        max_requests=max_requests,
        max_tool_calls=max_tool_calls,
    )
    # Convert TypedDict response to AgentOutput
    message_history = result.get("message_history")
    return AgentOutput(
        output=result["output"],
        message_history=list(message_history) if message_history else None,
        duration=result["duration"],
        usage=result.get("usage"),
        session_id=uuid.UUID(result["session_id"]),
    )


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

    This function delegates to AgentsClient.rank_items() via HTTP.

    Args:
        items: List of items to rank (each with 'id' and 'text').
        criteria_prompt: Natural language criteria for ranking.
        model_name: LLM model to use.
        model_provider: LLM provider.
        model_settings: Optional model settings.
        max_requests: Maximum number of LLM requests.
        retries: Number of retries on failure.
        base_url: Optional base URL for custom providers.
        min_items: Minimum number of items to return (optional).
        max_items: Maximum number of items to return (optional).

    Returns:
        List of item IDs in ranked order (most to least relevant).
    """
    from tracecat_registry.context import get_context

    ctx = get_context()
    return await ctx.agents.rank_items(
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
    """Rank items using multi-pass pairwise ranking with progressive refinement.

    This function delegates to AgentsClient.rank_items_pairwise() via HTTP.

    Args:
        items: List of items to rank (each with 'id' and 'text').
        criteria_prompt: Natural language criteria for ranking.
        model_name: LLM model to use.
        model_provider: LLM provider.
        id_field: Field name containing the item ID (default: "id").
        batch_size: Number of items per batch (default: 10).
        num_passes: Number of shuffle-batch-rank iterations (default: 10).
        refinement_ratio: Portion of top items to recursively refine (default: 0.5).
        model_settings: Optional model settings dict.
        max_requests: Maximum number of LLM requests per batch (default: 5).
        retries: Number of retries on failure (default: 3).
        base_url: Optional base URL for custom providers.
        min_items: Minimum number of items to return (optional).
        max_items: Maximum number of items to return (optional).

    Returns:
        List of item IDs in ranked order (most to least relevant).
    """
    from tracecat_registry.context import get_context

    ctx = get_context()
    return await ctx.agents.rank_items_pairwise(
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


class AgentsClient:
    """Client for Agent API operations including presets and execution."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    # --- Execution methods ---

    async def run(
        self,
        *,
        user_prompt: str,
        config: AgentConfig | None = None,
        preset_slug: str | None = None,
        max_requests: int = 120,
        max_tool_calls: int | None = None,
    ) -> registry_types.AgentOutputRead:
        """Run an AI agent.

        Either config or preset_slug must be provided.

        Args:
            user_prompt: The prompt for the agent.
            config: Inline agent configuration.
            preset_slug: Slug of a preset to use (resolves on server).
            max_requests: Maximum LLM requests.
            max_tool_calls: Maximum tool calls.

        Returns:
            Agent output with result, message history, usage, and session ID.
        """
        data: dict[str, Any] = {
            "user_prompt": user_prompt,
            "max_requests": max_requests,
        }
        if config is not None:
            data["config"] = asdict(config)
        if preset_slug is not None:
            data["preset_slug"] = preset_slug
        if max_tool_calls is not None:
            data["max_tool_calls"] = max_tool_calls

        return await self._client.post("/agent/run", json=data)

    async def rank_items(
        self,
        *,
        items: list[RankableItem],
        criteria_prompt: str,
        model_name: str,
        model_provider: str,
        model_settings: dict[str, object] | None = None,
        max_requests: int = 5,
        retries: int = 3,
        base_url: str | None = None,
        min_items: int | None = None,
        max_items: int | None = None,
    ) -> list[str | int]:
        """Rank items using an LLM based on natural language criteria.

        Args:
            items: List of items to rank (each with 'id' and 'text').
            criteria_prompt: Natural language criteria for ranking.
            model_name: LLM model name.
            model_provider: LLM provider.
            model_settings: Optional model settings.
            max_requests: Maximum LLM requests.
            retries: Number of retries on failure.
            base_url: Optional custom base URL.
            min_items: Minimum items to return (optional).
            max_items: Maximum items to return (optional).

        Returns:
            List of item IDs in ranked order.
        """
        data: dict[str, Any] = {
            "items": items,
            "criteria_prompt": criteria_prompt,
            "model_name": model_name,
            "model_provider": model_provider,
            "max_requests": max_requests,
            "retries": retries,
        }
        if model_settings is not None:
            data["model_settings"] = model_settings
        if base_url is not None:
            data["base_url"] = base_url
        if min_items is not None:
            data["min_items"] = min_items
        if max_items is not None:
            data["max_items"] = max_items

        return await self._client.post("/agent/rank", json=data)

    async def rank_items_pairwise(
        self,
        *,
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
        min_items: int | None = None,
        max_items: int | None = None,
    ) -> list[str | int]:
        """Rank items using pairwise LLM comparisons.

        Args:
            items: List of items to rank.
            criteria_prompt: Natural language criteria.
            model_name: LLM model name.
            model_provider: LLM provider.
            id_field: Field name for item ID (default: "id").
            batch_size: Items per batch (default: 10).
            num_passes: Number of shuffle-rank passes (default: 10).
            refinement_ratio: Top portion to refine (default: 0.5).
            model_settings: Optional model settings.
            max_requests: Maximum LLM requests per batch.
            retries: Number of retries on failure.
            base_url: Optional custom base URL.
            min_items: Minimum items to return (optional).
            max_items: Maximum items to return (optional).

        Returns:
            List of item IDs in ranked order.
        """
        data: dict[str, Any] = {
            "items": items,
            "criteria_prompt": criteria_prompt,
            "model_name": model_name,
            "model_provider": model_provider,
            "id_field": id_field,
            "batch_size": batch_size,
            "num_passes": num_passes,
            "refinement_ratio": refinement_ratio,
            "max_requests": max_requests,
            "retries": retries,
        }
        if model_settings is not None:
            data["model_settings"] = model_settings
        if base_url is not None:
            data["base_url"] = base_url
        if min_items is not None:
            data["min_items"] = min_items
        if max_items is not None:
            data["max_items"] = max_items

        return await self._client.post("/agent/rank-pairwise", json=data)

    # --- Preset methods ---

    async def list_presets(self) -> list[dict[str, Any]]:
        """List all agent presets in the workspace.

        Returns:
            List of preset metadata dictionaries.
        """
        return await self._client.get("/agent/presets")

    async def create_preset(
        self,
        *,
        name: str,
        model_name: str,
        model_provider: str,
        slug: str | Unset = UNSET,
        description: str | Unset = UNSET,
        instructions: str | Unset = UNSET,
        base_url: str | Unset = UNSET,
        output_type: str | dict[str, Any] | Unset = UNSET,
        actions: list[str] | Unset = UNSET,
    ) -> dict[str, Any]:
        """Create a new agent preset.

        Args:
            name: Human-readable name for the preset.
            model_name: LLM model name (e.g., 'gpt-4', 'claude-3-opus').
            model_provider: LLM provider identifier (e.g., 'openai', 'anthropic').
            slug: URL-friendly identifier. Auto-generated from name if not provided.
            description: Brief description of the preset's purpose.
            instructions: System instructions/prompt for the agent.
            base_url: Custom API endpoint URL for the model.
            output_type: Expected output format (type string or JSON schema).
            actions: List of action identifiers the agent can use as tools.

        Returns:
            Created preset data.
        """
        data: dict[str, Any] = {
            "name": name,
            "model_name": model_name,
            "model_provider": model_provider,
        }
        if is_set(slug):
            data["slug"] = slug
        if is_set(description):
            data["description"] = description
        if is_set(instructions):
            data["instructions"] = instructions
        if is_set(base_url):
            data["base_url"] = base_url
        if is_set(output_type):
            data["output_type"] = output_type
        if is_set(actions):
            data["actions"] = actions
        return await self._client.post("/agent/presets", json=data)

    async def get_preset(self, slug: str) -> dict[str, Any]:
        """Get an agent preset by slug.

        Args:
            slug: The preset's slug identifier.

        Returns:
            Preset data including all configuration.

        Raises:
            TracecatNotFoundError: If preset doesn't exist.
        """
        return await self._client.get(f"/agent/presets/by-slug/{slug}")

    async def update_preset(
        self,
        slug: str,
        *,
        name: str | Unset = UNSET,
        new_slug: str | Unset = UNSET,
        description: str | Unset = UNSET,
        instructions: str | Unset = UNSET,
        model_name: str | Unset = UNSET,
        model_provider: str | Unset = UNSET,
        base_url: str | Unset = UNSET,
        output_type: str | dict[str, Any] | Unset = UNSET,
        actions: list[str] | Unset = UNSET,
    ) -> dict[str, Any]:
        """Update an existing agent preset.

        Args:
            slug: The preset's current slug identifier.
            name: Updated name.
            new_slug: Updated slug identifier.
            description: Updated description.
            instructions: Updated system instructions.
            model_name: Updated LLM model name.
            model_provider: Updated LLM provider.
            base_url: Updated custom API endpoint URL.
            output_type: Updated output format.
            actions: Updated list of action identifiers.

        Returns:
            Updated preset data.

        Raises:
            TracecatNotFoundError: If preset doesn't exist.
        """
        data: dict[str, Any] = {}
        if is_set(name):
            data["name"] = name
        if is_set(new_slug):
            data["slug"] = new_slug
        if is_set(description):
            data["description"] = description
        if is_set(instructions):
            data["instructions"] = instructions
        if is_set(model_name):
            data["model_name"] = model_name
        if is_set(model_provider):
            data["model_provider"] = model_provider
        if is_set(base_url):
            data["base_url"] = base_url
        if is_set(output_type):
            data["output_type"] = output_type
        if is_set(actions):
            data["actions"] = actions
        return await self._client.patch(f"/agent/presets/by-slug/{slug}", json=data)

    async def delete_preset(self, slug: str) -> None:
        """Delete an agent preset.

        Args:
            slug: The preset's slug identifier.

        Raises:
            TracecatNotFoundError: If preset doesn't exist.
        """
        await self._client.delete(f"/agent/presets/by-slug/{slug}")


__all__ = [
    "AgentConfig",
    "AgentOutput",
    "AgentsClient",
    "MCPServerConfig",
    "OutputType",
    "RankableItem",
    "rank_items",
    "rank_items_pairwise",
    "run_agent",
]
