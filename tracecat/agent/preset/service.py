"""Service layer for managing agent presets."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from slugify import slugify
from sqlmodel import desc, func, select

from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.types import AgentConfig, OutputType
from tracecat.db.models import AgentPreset, RegistryAction
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
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

    async def create_preset(self, params: AgentPresetCreate) -> AgentPreset:
        """Create a new agent preset scoped to the current workspace."""

        slug = await self._normalize_and_validate_slug(
            proposed_slug=params.slug,
            fallback_name=params.name,
        )
        if params.actions:
            await self._validate_actions(params.actions)
        mcp_integrations = await self._validate_mcp_integrations(
            params.mcp_integrations
        )
        preset = AgentPreset(
            owner_id=self.workspace_id,
            slug=slug,
            name=params.name,
            description=params.description,
            instructions=params.instructions,
            model_name=params.model_name,
            model_provider=params.model_provider,
            base_url=params.base_url,
            output_type=params.output_type,
            actions=params.actions,
            namespaces=params.namespaces,
            tool_approvals=params.tool_approvals,
            mcp_integrations=mcp_integrations,
            retries=params.retries,
        )
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    async def _validate_actions(self, actions: list[str]) -> None:
        """Validate that all actions are in the registry."""
        actions_set = set(actions)
        stmt = select(RegistryAction).where(
            func.concat(RegistryAction.namespace, ".", RegistryAction.name).in_(
                actions_set
            )
        )
        result = await self.session.exec(stmt)
        registry_actions = result.all()
        available_identifiers = {a.action for a in registry_actions}
        if missing_actions := actions_set - available_identifiers:
            raise TracecatValidationError(
                f"{len(missing_actions)} actions were not found in the registry: {sorted(missing_actions)}"
            )

    async def update_preset(
        self, preset: AgentPreset, params: AgentPresetUpdate
    ) -> AgentPreset:
        """Update an existing preset."""
        set_fields = params.model_dump(exclude_unset=True)

        # Handle name first as it may be needed for slug fallback
        if "name" in set_fields:
            preset.name = set_fields.pop("name")

        # Handle slug with validation
        if "slug" in set_fields:
            preset.slug = await self._normalize_and_validate_slug(
                proposed_slug=set_fields.pop("slug"),
                fallback_name=preset.name,
                exclude_id=preset.id,
            )

        # Validate actions if provided
        if "actions" in set_fields:
            # Select in RegistryAction actions that are in the list of actions
            if actions := set_fields.pop("actions"):
                await self._validate_actions(actions)
            # If we reach this point, all actions are valid or was empty
            preset.actions = actions

        # Validate MCP integrations if provided
        if "mcp_integrations" in set_fields:
            preset.mcp_integrations = await self._validate_mcp_integrations(
                set_fields.pop("mcp_integrations")
            )

        # Update remaining fields
        for field, value in set_fields.items():
            setattr(preset, field, value)

        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    async def delete_preset(self, preset: AgentPreset) -> None:
        """Delete a preset."""
        await self.session.delete(preset)
        await self.session.commit()

    async def get_agent_config_by_slug(self, slug: str) -> AgentConfig:
        """Resolve the agent configuration for a preset by slug."""
        if preset := await self.get_preset_by_slug(slug):
            return self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with slug '{slug}' not found")

    async def get_agent_config(self, preset_id: uuid.UUID) -> AgentConfig:
        """Resolve the agent configuration for a preset by ID."""
        if preset := await self.get_preset(preset_id):
            return self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with ID '{preset_id}' not found")

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
            config = await self.get_agent_config(preset_id)
        else:
            config = await self.get_agent_config_by_slug(slug or "")
        return await self._attach_mcp_integration(config)

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

    async def _validate_mcp_integrations(
        self, provider_ids: list[str] | None
    ) -> list[str] | None:
        """Validate MCP OAuth integration provider IDs for the workspace."""
        if provider_ids is None:
            return None

        # Normalize and deduplicate while preserving order
        normalized = [pid.strip() for pid in provider_ids if pid and pid.strip()]
        unique_ids: list[str] = list(dict.fromkeys(normalized))
        if not unique_ids:
            return []

        integrations_service = IntegrationService(self.session, role=self.role)
        integrations = await integrations_service.list_integrations()
        available_provider_ids = {
            integration.provider_id for integration in integrations
        }
        missing = [pid for pid in unique_ids if pid not in available_provider_ids]
        if missing:
            raise TracecatValidationError(
                f"Unknown MCP integrations for this workspace: {sorted(missing)}"
            )

        return unique_ids

    async def get_preset(self, preset_id: uuid.UUID) -> AgentPreset | None:
        """Get an agent preset by ID with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.owner_id == self.workspace_id,
            AgentPreset.id == preset_id,
        )
        result = await self.session.exec(stmt)
        return result.first()

    async def get_preset_by_slug(self, slug: str) -> AgentPreset | None:
        """Get an agent preset by slug with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.owner_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        result = await self.session.exec(stmt)
        return result.first()

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
            mcp_integrations=preset.mcp_integrations,
            retries=preset.retries,
        )

    async def _attach_mcp_integration(self, config: AgentConfig) -> AgentConfig:
        """Resolve MCP provider URL and authorization header from selected integrations."""
        # Manual override already provided
        if not config.mcp_integrations:
            return config

        integrations_service = IntegrationService(self.session, role=self.role)
        # Preserve preset order when selecting the first available integration
        integrations = await integrations_service.list_integrations()
        by_provider = {
            integration.provider_id: integration for integration in integrations
        }
        selected_id = next(
            (pid for pid in config.mcp_integrations or [] if pid in by_provider), None
        )
        if not selected_id:
            raise TracecatValidationError(
                "No matching MCP integrations found for this preset in the workspace"
            )

        integration = by_provider[selected_id]
        provider_key = ProviderKey(
            id=integration.provider_id, grant_type=integration.grant_type
        )
        provider_impl = await integrations_service.resolve_provider_impl(
            provider_key=provider_key
        )
        if provider_impl is None or not issubclass(provider_impl, MCPAuthProvider):
            raise TracecatValidationError(
                f"Provider {integration.provider_id!r} is not a valid MCP provider"
            )

        await integrations_service.refresh_token_if_needed(integration)
        access_token = await integrations_service.get_access_token(integration)
        if not access_token:
            raise TracecatValidationError(
                f"MCP integration {integration.provider_id!r} has no access token"
            )

        token_type = integration.token_type or "Bearer"
        config.mcp_server_url = provider_impl.mcp_server_uri
        config.mcp_server_headers = {
            "Authorization": f"{token_type} {access_token.get_secret_value()}"
        }
        return config
