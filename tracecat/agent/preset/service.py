"""Service layer for managing agent presets."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from slugify import slugify
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlmodel import desc, func, select

from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.types import AgentConfig, OutputType
from tracecat.db.models import AgentPreset, RegistryAction
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService


class AgentPresetService(BaseWorkspaceService):
    """CRUD operations and helpers for agent presets."""

    service_name = "agent_preset"

    async def list_presets(self) -> Sequence[AgentPreset]:
        """Return all agent presets for the current workspace ordered by recency."""

        stmt = (
            select(AgentPreset)
            .where(AgentPreset.owner_id == self.workspace_id)
            .order_by(desc(AgentPreset.created_at))
        )
        result = await self.session.exec(stmt)
        return result.all()

    async def get_preset(self, preset_id: uuid.UUID) -> AgentPreset:
        """Retrieve a single preset by its identifier."""

        return await self._get_preset_model(preset_id)

    async def get_preset_by_slug(self, slug: str) -> AgentPreset:
        """Retrieve a preset by its slug."""

        return await self._get_preset_model_by_slug(slug)

    async def create_preset(self, params: AgentPresetCreate) -> AgentPreset:
        """Create a new agent preset scoped to the current workspace."""

        slug = await self._normalize_and_validate_slug(
            proposed_slug=params.slug,
            fallback_name=params.name,
        )
        if params.actions:
            await self._validate_actions(params.actions)
        preset = AgentPreset(
            owner_id=self.workspace_id,
            name=params.name,
            slug=slug,
            description=params.description,
            instructions=params.instructions,
            model_name=params.model_name,
            model_provider=params.model_provider,
            base_url=params.base_url,
            output_type=params.output_type,
            actions=params.actions,
            namespaces=params.namespaces,
            tool_approvals=params.tool_approvals,
            mcp_server_url=params.mcp_server_url,
            mcp_server_headers=params.mcp_server_headers,
            model_settings=params.model_settings,
            retries=params.retries,
        )
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    async def _validate_actions(self, actions: list[str]) -> None:
        """Validate that all actions are in the registry."""
        stmt = select(RegistryAction).where(
            func.concat(RegistryAction.namespace, ".", RegistryAction.name).in_(actions)
        )
        result = await self.session.exec(stmt)
        registry_actions = result.all()
        if len(registry_actions) != len(actions):
            raise TracecatValidationError(
                f"{len(actions)} actions were not found in the registry: {actions}"
            )

    async def update_preset(
        self, preset_id: uuid.UUID, params: AgentPresetUpdate
    ) -> AgentPreset:
        """Update an existing preset."""
        preset = await self._get_preset_model(preset_id)
        set_fields = params.model_dump(exclude_unset=True)

        # Handle name first as it may be needed for slug fallback
        if "name" in set_fields:
            preset.name = set_fields.pop("name")

        # Handle slug with validation
        if "slug" in set_fields:
            preset.slug = await self._normalize_and_validate_slug(
                proposed_slug=set_fields.pop("slug"),
                fallback_name=preset.name,
                exclude_id=preset_id,
            )

        # Validate actions if provided
        if "actions" in set_fields:
            actions = set_fields.pop("actions")
            # Select in RegistryAction actions that are in the list of actions
            if actions:
                self.logger.info("Validating actions", actions=actions)
                await self._validate_actions(actions)
            # If we reach this point, all actions are valid or was empty
            preset.actions = actions
        # Update remaining fields
        for field, value in set_fields.items():
            setattr(preset, field, value)

        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    async def delete_preset(self, preset_id: uuid.UUID) -> None:
        """Delete a preset."""

        preset = await self._get_preset_model(preset_id)
        await self.session.delete(preset)
        await self.session.commit()

    async def get_agent_config(self, preset_id: uuid.UUID) -> AgentConfig:
        """Resolve the agent configuration for a preset by ID."""

        preset = await self._get_preset_model(preset_id)
        return self._preset_to_agent_config(preset)

    async def get_agent_config_by_slug(self, slug: str) -> AgentConfig:
        """Resolve the agent configuration for a preset by slug."""

        preset = await self._get_preset_model_by_slug(slug)
        return self._preset_to_agent_config(preset)

    async def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
    ) -> AgentConfig:
        """Get an agent configuration from a preset by ID or slug."""
        if preset_id is None and slug is None:
            raise ValueError("Either preset_id or slug must be provided")

        if preset_id is not None:
            return await self.get_agent_config(preset_id)
        return await self.get_agent_config_by_slug(slug or "")

    async def _normalize_and_validate_slug(
        self,
        *,
        proposed_slug: str | None,
        fallback_name: str,
        exclude_id: uuid.UUID | None = None,
    ) -> str:
        base = proposed_slug or fallback_name
        slug = slugify(base, separator="-")
        if not slug:
            raise TracecatValidationError("Agent preset slug cannot be empty")

        stmt = select(AgentPreset).where(
            AgentPreset.owner_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        if exclude_id is not None:
            stmt = stmt.where(AgentPreset.id != exclude_id)

        result = await self.session.exec(stmt)
        if result.first() is not None:
            raise TracecatValidationError(
                f"Agent preset slug '{slug}' is already in use for this workspace",
            )
        return slug

    async def _get_preset_model(self, preset_id: uuid.UUID) -> AgentPreset:
        """Get an agent preset by ID with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.owner_id == self.workspace_id,
            AgentPreset.id == preset_id,
        )
        result = await self.session.exec(stmt)
        try:
            return result.one()
        except MultipleResultsFound as e:
            self.logger.error(
                "Multiple agent presets found",
                preset_id=preset_id,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                "Multiple agent presets found when searching by ID"
            ) from e
        except NoResultFound as e:
            self.logger.error(
                "Agent preset not found",
                preset_id=preset_id,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                f"Agent preset {preset_id} not found in this workspace"
            ) from e

    async def _get_preset_model_by_slug(self, slug: str) -> AgentPreset:
        """Get an agent preset by slug with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.owner_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        result = await self.session.exec(stmt)
        try:
            return result.one()
        except MultipleResultsFound as e:
            self.logger.error(
                "Multiple agent presets found",
                slug=slug,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                f"Multiple agent presets found with slug '{slug}'"
            ) from e
        except NoResultFound as e:
            self.logger.error(
                "Agent preset not found",
                slug=slug,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                f"Agent preset '{slug}' not found in this workspace"
            ) from e

    def _preset_to_agent_config(self, preset: AgentPreset) -> AgentConfig:
        return AgentConfig(
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            base_url=preset.base_url,
            instructions=preset.instructions,
            output_type=cast(OutputType | None, preset.output_type),
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            mcp_server_url=preset.mcp_server_url,
            mcp_server_headers=preset.mcp_server_headers,
            model_settings=preset.model_settings,
            retries=preset.retries,
        )
