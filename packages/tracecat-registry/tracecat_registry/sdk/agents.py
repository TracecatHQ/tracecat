"""Agent preset and skill SDK clients for registry actions."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

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


class CursorPage(TypedDict):
    items: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool


class AgentPresetSkillBinding(TypedDict):
    """Skill binding for attaching a published skill to an agent preset."""

    skill_id: str
    """Required skill identifier."""


class SkillPublishFile(TypedDict):
    """File payload for publishing a workspace skill version."""

    path: str
    content_base64: str
    content_type: NotRequired[str | None]


def _skill_identifier(skill_id: str, skill_uuid: str | uuid.UUID | None = None) -> str:
    """Return the skill route identifier, preferring stable UUID over authoring slug."""

    return str(skill_uuid) if skill_uuid is not None else skill_id


class AgentsClient:
    """Client for agent preset and skill API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    # --- Skill methods ---

    async def list_skills(
        self, *, limit: int = 20, cursor: str | None = None, reverse: bool = False
    ) -> CursorPage:
        params: dict[str, Any] = {"limit": limit, "reverse": reverse}
        if cursor is not None:
            params["cursor"] = cursor
        return await self._client.get("/agent/skills", params=params)

    async def create_skill(
        self, *, name: str, description: str | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"name": name}
        if description is not None:
            data["description"] = description
        return await self._client.post("/agent/skills", json=data)

    async def get_skill(
        self, skill_id: str, *, skill_uuid: str | uuid.UUID | None = None
    ) -> dict[str, Any]:
        identifier = _skill_identifier(skill_id, skill_uuid)
        return await self._client.get(f"/agent/skills/{identifier}")

    async def list_skill_versions(
        self,
        *,
        skill_id: str,
        skill_uuid: str | uuid.UUID | None = None,
        limit: int = 20,
        cursor: str | None = None,
        reverse: bool = False,
    ) -> CursorPage:
        identifier = _skill_identifier(skill_id, skill_uuid)
        params: dict[str, Any] = {"limit": limit, "reverse": reverse}
        if cursor is not None:
            params["cursor"] = cursor
        return await self._client.get(
            f"/agent/skills/{identifier}/versions", params=params
        )

    async def get_skill_version(
        self,
        *,
        skill_id: str,
        version_id: str | uuid.UUID,
        skill_uuid: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        identifier = _skill_identifier(skill_id, skill_uuid)
        return await self._client.get(
            f"/agent/skills/{identifier}/versions/{version_id}"
        )

    async def publish_skill_version(
        self,
        *,
        skill_id: str,
        files: list[SkillPublishFile] | list[dict[str, Any]],
        skill_uuid: str | uuid.UUID | None = None,
        base_version_id: str | None = None,
    ) -> dict[str, Any]:
        identifier = _skill_identifier(skill_id, skill_uuid)
        data: dict[str, Any] = {"files": files}
        if base_version_id is not None:
            data["base_version_id"] = base_version_id
        return await self._client.post(
            f"/agent/skills/{identifier}/versions",
            json=data,
        )

    async def restore_skill_version(
        self,
        *,
        skill_id: str,
        version_id: str | uuid.UUID,
        skill_uuid: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        identifier = _skill_identifier(skill_id, skill_uuid)
        return await self._client.post(
            f"/agent/skills/{identifier}/versions/{version_id}/restore"
        )

    async def archive_skill(
        self, skill_id: str, *, skill_uuid: str | uuid.UUID | None = None
    ) -> None:
        identifier = _skill_identifier(skill_id, skill_uuid)
        await self._client.delete(f"/agent/skills/{identifier}")

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
        model_name: str | Unset = UNSET,
        model_provider: str | Unset = UNSET,
        catalog_id: str | Unset = UNSET,
        slug: str | Unset = UNSET,
        description: str | Unset = UNSET,
        instructions: str | Unset = UNSET,
        base_url: str | Unset = UNSET,
        output_type: str | dict[str, Any] | Unset = UNSET,
        actions: list[str] | Unset = UNSET,
        namespaces: list[str] | Unset = UNSET,
        tool_approvals: dict[str, bool] | Unset = UNSET,
        mcp_integrations: list[str] | Unset = UNSET,
        agents: dict[str, Any] | Unset = UNSET,
        retries: int | Unset = UNSET,
        enable_thinking: bool | Unset = UNSET,
        enable_internet_access: bool | Unset = UNSET,
        skills: list[AgentPresetSkillBinding] | Unset = UNSET,
    ) -> dict[str, Any]:
        """Create a new agent preset.

        Args:
            name: Human-readable name for the preset.
            model_name: Deprecated legacy model name retained for backward
                compatibility. Prefer catalog_id. Defaults to workspace default
                if omitted.
            model_provider: Deprecated legacy model provider retained for backward
                compatibility. Prefer catalog_id. Defaults to workspace default
                if omitted.
            catalog_id: Canonical model catalog row ID backing this preset.
            slug: URL-friendly identifier. Auto-generated from name if not provided.
            description: Brief description of the preset's purpose.
            instructions: System instructions/prompt for the agent.
            base_url: Custom API endpoint URL for the model.
            output_type: Expected output format (type string or JSON schema).
            actions: List of action identifiers the agent can use as tools.
            skills: Skill bindings containing `skill_id`.

        Returns:
            Created preset data.
        """
        data: dict[str, Any] = {"name": name}
        # Deprecated legacy fields retained for backward compatibility.
        # catalog_id is the canonical model selector for new callers.
        if is_set(model_name):
            data["model_name"] = model_name
        if is_set(model_provider):
            data["model_provider"] = model_provider
        if is_set(catalog_id):
            data["catalog_id"] = catalog_id
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
        if is_set(namespaces):
            data["namespaces"] = namespaces
        if is_set(tool_approvals):
            data["tool_approvals"] = tool_approvals
        if is_set(mcp_integrations):
            data["mcp_integrations"] = mcp_integrations
        if is_set(agents):
            data["agents"] = agents
        if is_set(retries):
            data["retries"] = retries
        if is_set(enable_thinking):
            data["enable_thinking"] = enable_thinking
        if is_set(enable_internet_access):
            data["enable_internet_access"] = enable_internet_access
        if is_set(skills):
            data["skills"] = skills
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
        catalog_id: str | Unset = UNSET,
        base_url: str | Unset = UNSET,
        output_type: str | dict[str, Any] | Unset = UNSET,
        actions: list[str] | Unset = UNSET,
        namespaces: list[str] | Unset = UNSET,
        tool_approvals: dict[str, bool] | Unset = UNSET,
        mcp_integrations: list[str] | Unset = UNSET,
        agents: dict[str, Any] | Unset = UNSET,
        retries: int | Unset = UNSET,
        enable_thinking: bool | Unset = UNSET,
        enable_internet_access: bool | Unset = UNSET,
        skills: list[AgentPresetSkillBinding] | Unset = UNSET,
    ) -> dict[str, Any]:
        """Update an existing agent preset.

        Args:
            slug: The preset's current slug identifier.
            name: Updated name.
            new_slug: Updated slug identifier.
            description: Updated description.
            instructions: Updated system instructions.
            model_name: Deprecated legacy model name retained for backward
                compatibility. Prefer catalog_id.
            model_provider: Deprecated legacy model provider retained for backward
                compatibility. Prefer catalog_id.
            catalog_id: Canonical model catalog row ID backing this preset.
            base_url: Updated custom API endpoint URL.
            output_type: Updated output format.
            actions: Updated list of action identifiers.
            skills: Skill bindings containing `skill_id`.

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
        # Deprecated legacy fields retained for backward compatibility.
        # catalog_id is the canonical model selector for new callers.
        if is_set(model_name):
            data["model_name"] = model_name
        if is_set(model_provider):
            data["model_provider"] = model_provider
        if is_set(catalog_id):
            data["catalog_id"] = catalog_id
        if is_set(base_url):
            data["base_url"] = base_url
        if is_set(output_type):
            data["output_type"] = output_type
        if is_set(actions):
            data["actions"] = actions
        if is_set(namespaces):
            data["namespaces"] = namespaces
        if is_set(tool_approvals):
            data["tool_approvals"] = tool_approvals
        if is_set(mcp_integrations):
            data["mcp_integrations"] = mcp_integrations
        if is_set(agents):
            data["agents"] = agents
        if is_set(retries):
            data["retries"] = retries
        if is_set(enable_thinking):
            data["enable_thinking"] = enable_thinking
        if is_set(enable_internet_access):
            data["enable_internet_access"] = enable_internet_access
        if is_set(skills):
            data["skills"] = skills
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
    "AgentsClient",
    "AgentPresetSkillBinding",
    "CursorPage",
    "OutputType",
]
