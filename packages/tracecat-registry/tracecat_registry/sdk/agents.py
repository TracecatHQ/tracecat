"""Agent execution proxies and SDK client for registry actions."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel

from tracecat_registry import ActionIsInterfaceError
from tracecat_registry import config
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


class AgentsClient:
    """Client for Agent API operations including presets."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

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
    "build_agent",
    "rank_items",
    "rank_items_pairwise",
    "run_agent",
    "run_agent_sync",
]
