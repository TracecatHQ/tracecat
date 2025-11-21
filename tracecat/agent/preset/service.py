"""Service layer for managing agent presets."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from slugify import slugify
from sqlalchemy import func, select

from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.types import AgentConfig, MCPServerConfig, OutputType
from tracecat.db.models import AgentPreset, RegistryAction
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService


class AgentPresetService(BaseWorkspaceService):
    """CRUD operations and helpers for agent presets."""

    service_name = "agent_preset"

    async def list_presets(self) -> Sequence[AgentPreset]:
        """Return all agent presets for the current workspace ordered by recency."""

        stmt = (
            select(AgentPreset)
            .where(AgentPreset.owner_id == self.workspace_id)
            .order_by(AgentPreset.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create_preset(self, params: AgentPresetCreate) -> AgentPreset:
        """Create a new agent preset scoped to the current workspace."""

        slug = await self._normalize_and_validate_slug(
            proposed_slug=params.slug,
            fallback_name=params.name,
        )
        if params.actions:
            await self._validate_actions(params.actions)
        if params.mcp_integrations:
            await self._validate_mcp_integrations(params.mcp_integrations)
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
            mcp_integrations=params.mcp_integrations,
            model_settings=params.model_settings,
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
        result = await self.session.execute(stmt)
        registry_actions = result.scalars().all()
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

        if "mcp_integrations" in set_fields:
            if mcp_integrations := set_fields.pop("mcp_integrations"):
                await self._validate_mcp_integrations(mcp_integrations)
            preset.mcp_integrations = mcp_integrations

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
        """Get the agent configuration for a preset by slug."""
        if preset := await self.get_preset_by_slug(slug):
            return await self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with slug '{slug}' not found")

    async def get_agent_config(self, preset_id: uuid.UUID) -> AgentConfig:
        """Get the agent configuration for a preset by ID."""
        if preset := await self.get_preset(preset_id):
            return await self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with ID '{preset_id}' not found")

    async def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
    ) -> AgentConfig:
        """Get an agent configuration from a preset by ID or slug with MCP integrations resolved."""
        if preset_id is None and slug is None:
            raise ValueError("Either preset_id or slug must be provided")

        if preset_id is not None:
            return await self.get_agent_config(preset_id)
        return await self.get_agent_config_by_slug(slug or "")

    async def _validate_mcp_integrations(self, mcp_integrations: list[str]) -> None:
        """Validate MCP OAuth integration provider IDs for the workspace."""
        mcp_integrations_set = set(mcp_integrations)
        integrations_service = IntegrationService(self.session, role=self.role)
        integrations = await integrations_service.list_integrations()
        available_provider_ids = {
            integration.provider_id for integration in integrations
        }
        if missing_integrations := mcp_integrations_set - available_provider_ids:
            raise TracecatValidationError(
                f"{len(missing_integrations)} MCP integrations were not found in this workspace: {sorted(missing_integrations)}"
            )
        # Verify that each provider is actually an MCP provider
        # MCP providers follow the naming convention of ending with "_mcp"
        non_mcp_providers = [
            pid for pid in mcp_integrations_set if not pid.endswith("_mcp")
        ]
        if non_mcp_providers:
            raise TracecatValidationError(
                f"Providers are not MCP providers: {sorted(non_mcp_providers)}"
            )

    async def _resolve_mcp_integrations(
        self, mcp_integrations: list[str] | None
    ) -> list[MCPServerConfig] | None:
        """Resolve MCP provider URLs and authorization headers from selected integrations."""
        if not mcp_integrations:
            return None

        integrations_service = IntegrationService(self.session, role=self.role)
        integrations = await integrations_service.list_integrations()
        by_provider = {
            integration.provider_id: integration for integration in integrations
        }

        # Collect all matching integrations in preset order
        mcp_servers = []
        for provider_id in mcp_integrations:
            if provider_id not in by_provider:
                continue

            integration = by_provider[provider_id]
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
                logger.warning(
                    "Skipping MCP integration %r: no access token (likely disconnected)",
                    integration.provider_id,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "provider_id": integration.provider_id,
                        "integration_status": integration.status,
                    },
                )
                continue

            token_type = integration.token_type or "Bearer"
            mcp_servers.append(
                {
                    "url": provider_impl.mcp_server_uri,
                    "headers": {
                        "Authorization": f"{token_type} {access_token.get_secret_value()}"
                    },
                }
            )

        if not mcp_servers:
            raise TracecatValidationError(
                "No matching MCP integrations found for this preset in the workspace"
            )

        return mcp_servers

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

        result = await self.session.execute(stmt)
        if result.scalars().first() is not None:
            raise TracecatValidationError(
                f"Agent preset slug '{slug}' is already in use for this workspace",
            )
        return slug

    async def get_preset(self, preset_id: uuid.UUID) -> AgentPreset | None:
        """Get an agent preset by ID with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.owner_id == self.workspace_id,
            AgentPreset.id == preset_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_preset_by_slug(self, slug: str) -> AgentPreset | None:
        """Get an agent preset by slug with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.owner_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def _preset_to_agent_config(self, preset: AgentPreset) -> AgentConfig:
        mcp_servers = await self._resolve_mcp_integrations(preset.mcp_integrations)
        return AgentConfig(
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            base_url=preset.base_url,
            instructions=preset.instructions,
            output_type=cast(OutputType | None, preset.output_type),
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            model_settings=preset.model_settings,
            mcp_servers=mcp_servers,
            retries=preset.retries,
        )
