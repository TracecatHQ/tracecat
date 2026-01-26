"""Presets SDK client for Tracecat API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


class PresetsClient:
    """Client for Agent Presets API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

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
