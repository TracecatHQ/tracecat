"""Service layer for managing agent presets."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from slugify import slugify
from sqlalchemy import select

from tracecat.agent.common.types import MCPServerConfig
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.types import (
    AgentConfig,
    OutputType,
)
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentPreset,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.integrations.mcp_validation import (
    MCPValidationError,
    validate_mcp_command_config,
)
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement


class AgentPresetService(BaseWorkspaceService):
    """CRUD operations and helpers for agent presets."""

    service_name = "agent_preset"

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_presets(self) -> Sequence[AgentPreset]:
        """Return all agent presets for the current workspace ordered by recency."""

        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == self.workspace_id)
            .order_by(AgentPreset.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    @require_scope("agent:create")
    @audit_log(resource_type="agent_preset", action="create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
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
            workspace_id=self.workspace_id,
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
            enable_internet_access=params.enable_internet_access,
            retries=params.retries,
        )
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    async def _validate_actions(self, actions: list[str]) -> None:
        """Validate that all actions are in the registry index."""
        actions_set = set(actions)
        registry_service = RegistryActionsService(self.session, role=self.role)
        index_entries = await registry_service.list_actions_from_index(
            include_keys=actions_set
        )
        available_identifiers = {
            f"{entry.namespace}.{entry.name}" for entry, _ in index_entries
        }
        if missing_actions := actions_set - available_identifiers:
            raise TracecatValidationError(
                f"{len(missing_actions)} actions were not found in the registry: {sorted(missing_actions)}"
            )

    @require_scope("agent:update")
    @audit_log(resource_type="agent_preset", action="update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
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

    @require_scope("agent:delete")
    @audit_log(resource_type="agent_preset", action="delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_preset(self, preset: AgentPreset) -> None:
        """Delete a preset."""
        await self.session.delete(preset)
        await self.session.commit()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_agent_config_by_slug(self, slug: str) -> AgentConfig:
        """Get the agent configuration for a preset by slug."""
        if preset := await self.get_preset_by_slug(slug):
            return await self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with slug '{slug}' not found")

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_agent_config(self, preset_id: uuid.UUID) -> AgentConfig:
        """Get the agent configuration for a preset by ID."""
        if preset := await self.get_preset(preset_id):
            return await self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with ID '{preset_id}' not found")

    @requires_entitlement(Entitlement.AGENT_ADDONS)
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
        """Validate MCP integration IDs for the workspace."""
        if not mcp_integrations:
            return

        # Convert string IDs to UUIDs for validation
        mcp_integration_ids = set()
        for mcp_id in mcp_integrations:
            try:
                mcp_integration_ids.add(uuid.UUID(mcp_id))
            except ValueError as err:
                raise TracecatValidationError(
                    f"Invalid MCP integration ID format: {mcp_id}"
                ) from err

        integrations_service = IntegrationService(self.session, role=self.role)
        available_mcp_integrations = await integrations_service.list_mcp_integrations()
        available_mcp_integration_ids = {
            mcp_integration.id for mcp_integration in available_mcp_integrations
        }

        # Check if all requested IDs exist
        if missing_ids := mcp_integration_ids - available_mcp_integration_ids:
            missing_str = sorted(str(id) for id in missing_ids)
            raise TracecatValidationError(
                f"{len(missing_ids)} MCP integrations were not found in this workspace: {missing_str}"
            )

    async def _resolve_mcp_integrations(
        self, mcp_integrations: list[str] | None
    ) -> list[MCPServerConfig] | None:
        """Resolve MCP integrations into MCP server configs."""
        if not mcp_integrations:
            return None

        integrations_service = IntegrationService(self.session, role=self.role)
        available_mcp_integrations = await integrations_service.list_mcp_integrations()
        by_id = {
            mcp_integration.id: mcp_integration
            for mcp_integration in available_mcp_integrations
        }

        mcp_servers: list[MCPServerConfig] = []

        for mcp_id_str in mcp_integrations:
            try:
                mcp_integration_id = uuid.UUID(mcp_id_str)
            except ValueError:
                logger.warning(
                    "Invalid MCP integration ID format, skipping: %r",
                    mcp_id_str,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_id": mcp_id_str,
                    },
                )
                continue

            if mcp_integration_id not in by_id:
                logger.warning(
                    "MCP integration not found, skipping: %r",
                    mcp_integration_id,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration_id),
                    },
                )
                continue

            mcp_integration = by_id[mcp_integration_id]

            # Handle stdio-type servers
            if mcp_integration.server_type == "stdio":
                if not mcp_integration.stdio_command:
                    logger.warning(
                        "Stdio-type MCP integration %r has no stdio_command specified",
                        mcp_integration.name,
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue

                # Decrypt stdio_env if present
                stdio_env = integrations_service.decrypt_stdio_env(mcp_integration)
                if stdio_env:
                    try:
                        stdio_env = await integrations_service.resolve_mcp_stdio_env(
                            stdio_env=stdio_env,
                            mcp_integration_id=mcp_integration.id,
                            mcp_integration_slug=mcp_integration.slug,
                        )
                    except Exception as e:
                        logger.warning(
                            "Stdio env resolution failed for MCP integration %r: %s",
                            mcp_integration.name,
                            str(e),
                            extra={
                                "workspace_id": str(self.workspace_id),
                                "mcp_integration_id": str(mcp_integration.id),
                                "mcp_integration_slug": mcp_integration.slug,
                                "env_keys": sorted(stdio_env.keys()),
                            },
                        )
                        continue

                # Re-validate command config at resolution time
                try:
                    validate_mcp_command_config(
                        command=mcp_integration.stdio_command,
                        args=mcp_integration.stdio_args,
                        env=stdio_env,
                        name=mcp_integration.slug,
                    )
                except MCPValidationError as e:
                    logger.warning(
                        "Stdio-type MCP integration %r failed validation: %s",
                        mcp_integration.name,
                        str(e),
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue

                command_config: MCPServerConfig = {
                    "type": "stdio",
                    "name": mcp_integration.slug,
                    "command": mcp_integration.stdio_command,
                }
                if mcp_integration.stdio_args:
                    command_config["args"] = mcp_integration.stdio_args
                if stdio_env:
                    command_config["env"] = stdio_env
                if mcp_integration.timeout:
                    command_config["timeout"] = mcp_integration.timeout

                mcp_servers.append(command_config)
                continue

            if http_config := await integrations_service.resolve_mcp_http_server_config(
                mcp_integration=mcp_integration,
                server_name=mcp_integration.name,
            ):
                mcp_servers.append(http_config)

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
            AgentPreset.workspace_id == self.workspace_id,
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

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_preset(self, preset_id: uuid.UUID) -> AgentPreset | None:
        """Get an agent preset by ID with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.id == preset_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_preset_by_slug(self, slug: str) -> AgentPreset | None:
        """Get an agent preset by slug with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def _preset_to_agent_config(self, preset: AgentPreset) -> AgentConfig:
        mcp_servers = await self._resolve_mcp_integrations(preset.mcp_integrations)
        # Only disable parallel tool calls if tools will be present
        model_settings = {}
        if preset.actions or mcp_servers:
            model_settings["parallel_tool_calls"] = False
        return AgentConfig(
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            base_url=preset.base_url,
            instructions=preset.instructions,
            output_type=cast(OutputType | None, preset.output_type),
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            mcp_servers=mcp_servers,
            retries=preset.retries,
            model_settings=model_settings,
            enable_internet_access=preset.enable_internet_access,
        )
