"""Agent SDK client for Tracecat API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


class AgentClient:
    """Client for Agent API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    # === Agent Preset CRUD === #

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
            name: Human-readable name for the agent preset.
            model_name: The LLM model name.
            model_provider: The LLM provider identifier.
            slug: URL-friendly identifier (auto-generated if not provided).
            description: Brief description of the preset.
            instructions: System instructions/prompt for the agent.
            base_url: Custom API endpoint URL for the model.
            output_type: Expected output format (type string or JSON schema).
            actions: List of action identifiers available to the agent.

        Returns:
            Created agent preset data.
        """
        payload: dict[str, Any] = {
            "name": name,
            "model_name": model_name,
            "model_provider": model_provider,
        }
        if is_set(slug):
            payload["slug"] = slug
        if is_set(description):
            payload["description"] = description
        if is_set(instructions):
            payload["instructions"] = instructions
        if is_set(base_url):
            payload["base_url"] = base_url
        if is_set(output_type):
            payload["output_type"] = output_type
        if is_set(actions):
            payload["actions"] = actions

        return await self._client.post("/agent/presets", json=payload)

    async def get_preset_by_slug(self, slug: str) -> dict[str, Any]:
        """Get an agent preset by slug.

        Args:
            slug: The agent preset slug.

        Returns:
            Agent preset data.
        """
        return await self._client.get(f"/agent/presets/by-slug/{slug}")

    async def list_presets(self) -> list[dict[str, Any]]:
        """List all agent presets.

        Returns:
            List of agent preset data.
        """
        return await self._client.get("/agent/presets")

    async def update_preset(
        self,
        slug: str,
        *,
        name: str | Unset = UNSET,
        model_name: str | Unset = UNSET,
        model_provider: str | Unset = UNSET,
        new_slug: str | Unset = UNSET,
        description: str | Unset = UNSET,
        instructions: str | Unset = UNSET,
        base_url: str | Unset = UNSET,
        output_type: str | dict[str, Any] | Unset = UNSET,
        actions: list[str] | Unset = UNSET,
    ) -> dict[str, Any]:
        """Update an agent preset.

        Args:
            slug: The preset slug to update.
            name: Updated name.
            model_name: Updated model name.
            model_provider: Updated provider.
            new_slug: Updated slug identifier.
            description: Updated description.
            instructions: Updated instructions.
            base_url: Updated base URL.
            output_type: Updated output type.
            actions: Updated actions list.

        Returns:
            Updated agent preset data.
        """
        payload: dict[str, Any] = {}
        if is_set(name):
            payload["name"] = name
        if is_set(model_name):
            payload["model_name"] = model_name
        if is_set(model_provider):
            payload["model_provider"] = model_provider
        if is_set(new_slug):
            payload["slug"] = new_slug
        if is_set(description):
            payload["description"] = description
        if is_set(instructions):
            payload["instructions"] = instructions
        if is_set(base_url):
            payload["base_url"] = base_url
        if is_set(output_type):
            payload["output_type"] = output_type
        if is_set(actions):
            payload["actions"] = actions

        return await self._client.patch(
            f"/agent/presets/by-slug/{slug}",
            json=payload,
        )

    async def delete_preset(self, slug: str) -> None:
        """Delete an agent preset.

        Args:
            slug: The preset slug to delete.
        """
        await self._client.delete(f"/agent/presets/by-slug/{slug}")

    async def run_action(
        self,
        *,
        user_prompt: str,
        model_name: str,
        model_provider: str,
        instructions: str | None = None,
        output_type: str | dict[str, Any] | None = None,
        model_settings: dict[str, Any] | None = None,
        max_requests: int = 20,
        retries: int = 6,
        base_url: str | None = None,
    ) -> Any:
        """Run an agent action without tool calling support.

        Args:
            user_prompt: The user prompt to the agent.
            model_name: The name of the model to use.
            model_provider: The provider of the model to use.
            instructions: System instructions for the agent.
            output_type: Expected output format (type string or JSON schema).
            model_settings: Model-specific configuration parameters.
            max_requests: Maximum number of requests for the agent.
            retries: Maximum number of retry attempts.
            base_url: Custom base URL for the model provider's API.

        Returns:
            Agent execution result.
        """
        payload: dict[str, Any] = {
            "user_prompt": user_prompt,
            "model_name": model_name,
            "model_provider": model_provider,
            "instructions": instructions,
            "output_type": output_type,
            "model_settings": model_settings,
            "max_requests": max_requests,
            "retries": retries,
            "base_url": base_url,
        }
        return await self._client.post("/agent/action", json=payload)

    async def run_agent(
        self,
        *,
        user_prompt: str,
        model_name: str,
        model_provider: str,
        instructions: str | None = None,
        output_type: str | dict[str, Any] | None = None,
        model_settings: dict[str, Any] | None = None,
        max_requests: int = 20,
        max_tool_calls: int | None = None,
        retries: int = 6,
        base_url: str | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        actions: list[str] | None = None,
        namespaces: list[str] | None = None,
        tool_approvals: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Run an AI agent with full feature support including MCP servers and tool calling.

        Args:
            user_prompt: The user prompt to the agent.
            model_name: The name of the model to use.
            model_provider: The provider of the model to use.
            instructions: System instructions for the agent.
            output_type: Expected output format (type string or JSON schema).
            model_settings: Model-specific configuration parameters.
            max_requests: Maximum number of requests for the agent.
            max_tool_calls: Maximum number of tool calls for the agent.
            retries: Maximum number of retry attempts.
            base_url: Custom base URL for the model provider's API.
            mcp_servers: List of MCP server configurations with 'url' and optional 'headers'.
            actions: List of action identifiers available to the agent.
            namespaces: Optional list of namespaces to restrict available tools.
            tool_approvals: Optional per-tool approval requirements keyed by action name.

        Returns:
            Agent execution result with output, message_history, duration, usage, and session_id.
        """
        payload: dict[str, Any] = {
            "user_prompt": user_prompt,
            "model_name": model_name,
            "model_provider": model_provider,
        }
        if instructions is not None:
            payload["instructions"] = instructions
        if output_type is not None:
            payload["output_type"] = output_type
        if model_settings is not None:
            payload["model_settings"] = model_settings
        if max_requests != 20:
            payload["max_requests"] = max_requests
        if max_tool_calls is not None:
            payload["max_tool_calls"] = max_tool_calls
        if retries != 6:
            payload["retries"] = retries
        if base_url is not None:
            payload["base_url"] = base_url
        if mcp_servers is not None:
            payload["mcp_servers"] = mcp_servers
        if actions is not None:
            payload["actions"] = actions
        if namespaces is not None:
            payload["namespaces"] = namespaces
        if tool_approvals is not None:
            payload["tool_approvals"] = tool_approvals

        return await self._client.post("/agent/run", json=payload)
