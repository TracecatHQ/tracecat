"""Service layer for managing agent presets."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa
from slugify import slugify
from sqlalchemy import column, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import selectinload

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.channels.service import AgentChannelService
from tracecat.agent.common.types import MCPHttpServerConfig, MCPServerConfig
from tracecat.agent.preset.resolver import resolve_agents_config
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetSkillBindingBase,
    AgentPresetSkillBindingChange,
    AgentPresetSkillBindingRead,
    AgentPresetUpdate,
    AgentPresetVersionDiff,
    AgentPresetVersionRead,
    AgentPresetVersionReadMinimal,
    ScalarFieldChange,
    StringListFieldChange,
    ToolApprovalFieldChange,
    _agent_preset_capabilities,
    build_subagent_eligibility,
)
from tracecat.agent.preset.types import SkillBindingSpec
from tracecat.agent.skill.service import SkillService
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.agent.types import (
    AgentConfig,
    OutputType,
)
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentCatalog,
    AgentPreset,
    AgentPresetSkill,
    AgentPresetVersion,
    AgentPresetVersionSkill,
    Skill,
    SkillVersion,
)
from tracecat.db.soft_delete import with_deleted
from tracecat.dsl.common import create_default_execution_context
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.executor.service import get_workspace_variables
from tracecat.expressions.eval import collect_expressions, eval_templated_object
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.mcp_validation import (
    MCPConfigurationError,
    MCPSecretResolutionError,
    MCPValidationError,
    validate_mcp_command_config,
)
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets import secrets_manager
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.settings.schemas import VersionedResourceResolutionStrategy
from tracecat.settings.service import get_versioned_resource_resolution_strategy
from tracecat.tiers.enums import Entitlement

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role


class AgentPresetService(BaseWorkspaceService):
    """CRUD operations and helpers for agent presets."""

    service_name = "agent_preset"
    EXECUTION_FIELDS = {
        "instructions",
        "model_name",
        "model_provider",
        "catalog_id",
        "base_url",
        "output_type",
        "actions",
        "namespaces",
        "tool_approvals",
        "mcp_integrations",
        "agents",
        "retries",
        "enable_thinking",
        "enable_internet_access",
    }

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        self.skills = SkillService(session, role=self.role)

    async def use_latest_resource_versions(self) -> bool:
        """Return whether preset dependencies should resolve to current versions."""

        strategy = await get_versioned_resource_resolution_strategy(
            role=self.role,
            session=self.session,
        )
        return strategy is VersionedResourceResolutionStrategy.LATEST

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_presets(self) -> Sequence[AgentPreset]:
        """Return all agent presets for the current workspace ordered by recency."""

        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == self.workspace_id)
            .where(AgentPreset.deleted_at.is_(None))
            .order_by(AgentPreset.created_at.desc())
            .options(selectinload(AgentPreset.tags))
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def _list_skill_bindings(
        self,
        *,
        binding_model: type[AgentPresetSkill] | type[AgentPresetVersionSkill],
        owner_column: Any,
        owner_id: uuid.UUID,
    ) -> list[AgentPresetSkillBindingRead]:
        """Return resolved skill bindings for a preset head or immutable version."""

        stmt = (
            select(
                binding_model.skill_id,
                SkillVersion.name,
                binding_model.skill_version_id,
                SkillVersion.version,
            )
            .join(SkillVersion, binding_model.skill_version_id == SkillVersion.id)
            .where(
                binding_model.workspace_id == self.workspace_id,
                owner_column == owner_id,
            )
            .order_by(SkillVersion.name.asc(), binding_model.skill_id.asc())
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        return [
            AgentPresetSkillBindingRead(
                skill_id=skill_id,
                skill_name=skill_name,
                skill_version_id=skill_version_id,
                skill_version=skill_version,
            )
            for skill_id, skill_name, skill_version_id, skill_version in rows
            if skill_name is not None
        ]

    async def _list_head_skill_bindings(
        self, preset_id: uuid.UUID
    ) -> list[AgentPresetSkillBindingRead]:
        """Return mutable skill bindings for a preset head."""

        return await self._list_skill_bindings(
            binding_model=AgentPresetSkill,
            owner_column=AgentPresetSkill.preset_id,
            owner_id=preset_id,
        )

    async def _list_version_skill_bindings(
        self, version_id: uuid.UUID
    ) -> list[AgentPresetSkillBindingRead]:
        """Return exact skill version refs for an immutable preset version."""

        return await self._list_skill_bindings(
            binding_model=AgentPresetVersionSkill,
            owner_column=AgentPresetVersionSkill.preset_version_id,
            owner_id=version_id,
        )

    async def build_preset_read(self, preset: AgentPreset) -> AgentPresetRead:
        """Build the response model for a preset."""

        agents = AgentSubagentsConfig.model_validate(preset.agents)
        return AgentPresetRead(
            id=preset.id,
            workspace_id=preset.workspace_id,
            name=preset.name,
            slug=preset.slug,
            description=preset.description,
            current_version_id=preset.current_version_id,
            folder_id=preset.folder_id,
            instructions=preset.instructions,
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            catalog_id=preset.catalog_id,
            base_url=preset.base_url,
            output_type=cast(OutputType | None, preset.output_type),
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            mcp_integrations=preset.mcp_integrations,
            agents=agents,
            retries=preset.retries,
            enable_thinking=preset.enable_thinking,
            enable_internet_access=preset.enable_internet_access,
            created_at=preset.created_at,
            updated_at=preset.updated_at,
            skills=await self._list_head_skill_bindings(preset.id),
        )

    async def build_version_read(
        self, version: AgentPresetVersion
    ) -> AgentPresetVersionRead:
        """Build the response model for an immutable preset version."""

        agents = AgentSubagentsConfig.model_validate(version.agents)
        return AgentPresetVersionRead(
            id=version.id,
            preset_id=version.preset_id,
            workspace_id=version.workspace_id,
            version=version.version,
            instructions=version.instructions,
            model_name=version.model_name,
            model_provider=version.model_provider,
            catalog_id=version.catalog_id,
            base_url=version.base_url,
            output_type=cast(OutputType | None, version.output_type),
            actions=version.actions,
            namespaces=version.namespaces,
            tool_approvals=version.tool_approvals,
            mcp_integrations=version.mcp_integrations,
            agents=agents,
            retries=version.retries,
            enable_thinking=version.enable_thinking,
            enable_internet_access=version.enable_internet_access,
            capabilities=_agent_preset_capabilities(
                agents_config=agents,
                tool_approvals=version.tool_approvals,
                enable_internet_access=version.enable_internet_access,
            ),
            subagent_eligibility=build_subagent_eligibility(
                agents_config=agents,
                tool_approvals=version.tool_approvals,
            ),
            created_at=version.created_at,
            updated_at=version.updated_at,
            skills=await self._list_version_skill_bindings(version.id),
        )

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
            await self.validate_mcp_integrations(params.mcp_integrations)
        if params.skills:
            await self.skills.validate_binding_inputs(
                params.skills,
                for_update=True,
            )
        catalog_entry: AgentCatalog | None = None
        if params.catalog_id is not None:
            catalog_entry = await self._get_enabled_catalog_entry(params.catalog_id)
        preset = AgentPreset(
            workspace_id=self.workspace_id,
            slug=slug,
            name=params.name,
            description=params.description,
            instructions=params.instructions,
            model_name=(
                catalog_entry.model_name
                if catalog_entry is not None
                else params.model_name
            ),
            model_provider=(
                catalog_entry.model_provider
                if catalog_entry is not None
                else params.model_provider
            ),
            catalog_id=params.catalog_id,
            base_url=params.base_url,
            output_type=params.output_type,
            actions=params.actions,
            namespaces=params.namespaces,
            tool_approvals=params.tool_approvals,
            mcp_integrations=params.mcp_integrations,
            agents=AgentSubagentsConfig().model_dump(mode="json"),
            enable_thinking=params.enable_thinking,
            enable_internet_access=params.enable_internet_access,
            retries=params.retries,
        )
        self.session.add(preset)
        await self.session.flush()
        preset.agents = await self._resolve_preset_subagent_configs(
            params.agents,
            parent_preset_id=preset.id,
            parent_slug=slug,
        )
        if params.skills is not None:
            binding_specs = await self._binding_specs_from_inputs(
                params.skills,
                for_update=True,
            )
            await self._replace_head_skill_bindings(
                preset.id,
                params.skills,
                binding_specs=binding_specs,
            )
        version = await self._create_version_from_preset(preset)
        preset.current_version_id = version.id
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

    async def _get_enabled_catalog_entry(self, catalog_id: uuid.UUID) -> AgentCatalog:
        """Return an org-visible catalog row after validating workspace access."""
        access_service = AgentModelAccessService(session=self.session, role=self.role)
        if not await access_service.is_catalog_enabled(
            catalog_id,
            workspace_id=self.workspace_id,
        ):
            raise TracecatValidationError(
                f"Catalog entry {catalog_id} is not enabled for this workspace"
            )
        stmt = select(AgentCatalog).where(
            AgentCatalog.id == catalog_id,
            sa.or_(
                AgentCatalog.organization_id.is_(None),
                AgentCatalog.organization_id == self.organization_id,
            ),
        )
        catalog_entry = (await self.session.execute(stmt)).scalar_one_or_none()
        if catalog_entry is None:
            raise TracecatValidationError(
                f"Catalog entry {catalog_id} is not enabled for this workspace"
            )
        return catalog_entry

    @require_scope("agent:update")
    @audit_log(resource_type="agent_preset", action="update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def update_preset(
        self, preset: AgentPreset, params: AgentPresetUpdate
    ) -> AgentPreset:
        """Update an existing preset."""
        await self._lock_preset_row(preset.id)
        set_fields = params.model_dump(exclude_unset=True, exclude={"skills"})
        execution_changed = False
        requested_skills = None
        preset_locked = True
        if "skills" in params.model_fields_set:
            requested_skills = params.skills or []

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
            if preset.actions != actions:
                preset.actions = actions
                execution_changed = True

        if "mcp_integrations" in set_fields:
            if mcp_integrations := set_fields.pop("mcp_integrations"):
                await self.validate_mcp_integrations(mcp_integrations)
            if preset.mcp_integrations != mcp_integrations:
                preset.mcp_integrations = mcp_integrations
                execution_changed = True

        if "agents" in set_fields:
            agents = await self._resolve_preset_subagent_configs(
                set_fields.pop("agents"),
                parent_preset_id=preset.id,
                parent_slug=preset.slug,
            )
            if preset.agents != agents:
                preset.agents = agents
                execution_changed = True

        if requested_skills is not None:
            await self.skills.validate_binding_inputs(
                requested_skills,
                for_update=True,
            )
            current_specs = await self._get_head_skill_binding_specs(preset.id)
            requested_specs = await self._binding_specs_from_inputs(
                requested_skills,
                for_update=True,
            )
            if current_specs != requested_specs:
                await self._replace_head_skill_bindings(
                    preset.id,
                    requested_skills,
                    binding_specs=requested_specs,
                )
                execution_changed = True
        effective_catalog_id = None
        if "catalog_id" in set_fields:
            effective_catalog_id = set_fields["catalog_id"]
        elif "model_name" in set_fields or "model_provider" in set_fields:
            effective_catalog_id = preset.catalog_id
        if effective_catalog_id is not None:
            catalog_entry = await self._get_enabled_catalog_entry(effective_catalog_id)
            set_fields["model_name"] = catalog_entry.model_name
            set_fields["model_provider"] = catalog_entry.model_provider

        # Update remaining fields
        for field, value in set_fields.items():
            if getattr(preset, field) != value:
                if field in self.EXECUTION_FIELDS:
                    execution_changed = True
                setattr(preset, field, value)

        self.session.add(preset)
        if execution_changed:
            version = await self._create_version_from_preset(
                preset,
                preset_locked=preset_locked,
            )
            preset.current_version_id = version.id
            self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    @require_scope("agent:delete")
    @audit_log(resource_type="agent_preset", action="delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_preset(self, preset: AgentPreset) -> None:
        """Soft-delete a preset without deleting its published versions."""
        await self._lock_preset_row(preset.id)
        await self._ensure_not_referenced_as_subagent(preset)
        channel_service = AgentChannelService(self.session, role=self.role)
        await channel_service.deactivate_tokens_for_preset(preset.id)
        preset.deleted_at = datetime.now(UTC)
        self.session.add(preset)
        await self.session.commit()

    async def _ensure_not_referenced_as_subagent(self, preset: AgentPreset) -> None:
        """Block deletion while other preset heads still reference this preset."""
        reference_count = await self._count_head_subagent_references(preset)
        if reference_count > 0:
            raise TracecatValidationError(
                "Cannot delete an agent preset that is still referenced as a subagent",
                detail={
                    "code": "preset_in_use_as_subagent",
                    "head_reference_count": reference_count,
                },
            )

    async def _count_head_subagent_references(self, preset: AgentPreset) -> int:
        subagent_ref_exists = self._subagent_reference_exists(
            AgentPreset.agents,
            preset_id=preset.id,
            slug=preset.slug,
        )
        stmt = (
            select(func.count())
            .select_from(AgentPreset)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id != preset.id,
                AgentPreset.deleted_at.is_(None),
                subagent_ref_exists,
            )
        )
        return (await self.session.execute(stmt)).scalar_one()

    @staticmethod
    def _subagent_reference_exists(
        agents: sa.SQLColumnExpression[dict[str, Any]],
        *,
        preset_id: uuid.UUID,
        slug: str,
    ) -> sa.ColumnElement[bool]:
        subagents_value = agents["subagents"]
        subagents_array = sa.case(
            (func.jsonb_typeof(subagents_value) == "array", subagents_value),
            else_=literal([], type_=JSONB),
        )
        subagents = (
            func.jsonb_array_elements(subagents_array)
            .table_valued(column("value", JSONB))
            .alias("subagent")
        )
        return (
            select(literal(True))
            .select_from(subagents)
            .where(
                sa.or_(
                    subagents.c.value["preset_id"].astext == str(preset_id),
                    sa.and_(
                        subagents.c.value["preset_id"].astext.is_(None),
                        subagents.c.value["preset"].astext == slug,
                    ),
                )
            )
            .exists()
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> AgentConfig:
        """Get an agent configuration from a preset by ID or slug with MCP integrations resolved."""
        version = await self.resolve_agent_preset_version(
            preset_id=preset_id,
            slug=slug,
            preset_version_id=preset_version_id,
            preset_version=preset_version,
        )
        return await self._version_to_agent_config(version)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def resolve_agent_preset_version(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> AgentPresetVersion:
        """Resolve a preset version from logical preset identity and optional pin."""
        if preset_id is None and slug is None and preset_version_id is None:
            raise ValueError(
                "Either preset_id, slug, or preset_version_id must be provided"
            )
        if preset_version is not None and slug is None and preset_id is None:
            raise ValueError("'preset_version' requires a preset_id or slug")

        preset: AgentPreset | None = None
        if preset_id is not None:
            preset = await self.get_preset(preset_id)
        elif slug is not None:
            preset = await self.get_preset_by_slug(slug)

        if preset is None and (preset_id is not None or slug is not None):
            detail = slug if slug is not None else str(preset_id)
            raise TracecatNotFoundError(f"Agent preset '{detail}' not found")

        if preset_version_id is not None:
            version = await self.get_active_version(version_id=preset_version_id)
            if version is None:
                raise TracecatNotFoundError(
                    f"Agent preset version with ID '{preset_version_id}' not found"
                )
            if preset is not None and version.preset_id != preset.id:
                raise TracecatValidationError(
                    "Preset version does not belong to the selected preset"
                )
            return version

        if preset is None:
            raise TracecatNotFoundError("Agent preset not found")

        if preset_version is not None:
            version = await self.get_version_by_number(
                preset_id=preset.id,
                version=preset_version,
            )
            if version is None:
                raise TracecatNotFoundError(
                    f"Agent preset version {preset_version} not found"
                )
            return version

        return await self.get_current_version_for_preset(preset)

    async def validate_mcp_integrations(self, mcp_integrations: list[str]) -> None:
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

    async def _resolve_preset_subagent_configs(
        self,
        agents: AgentSubagentsConfig | dict[str, Any] | None,
        *,
        parent_preset_id: uuid.UUID,
        parent_slug: str,
    ) -> dict[str, Any]:
        """Resolve and validate a preset's subagent configuration."""
        config = AgentSubagentsConfig.model_validate({} if agents is None else agents)
        # Persisted refs (e.g. restored historical configs) already carry preset
        # ids; validate them before resolution, which would otherwise surface an
        # soft-deleted child as a generic version-not-found error.
        persisted_refs = [
            ref
            for ref in config.subagents
            if isinstance(ref, ResolvedAttachedSubagentRef)
        ]
        if persisted_refs:
            await self._lock_active_subagent_presets(
                ResolvedAgentsConfig(enabled=True, subagents=persisted_refs)
            )
        resolved = await resolve_agents_config(
            self,
            agents=config,
            parent_preset_id=parent_preset_id,
            parent_slug=parent_slug,
        )
        binding = resolved.to_agents_binding()
        await self._lock_active_subagent_presets(binding)
        return binding.model_dump(mode="json")

    async def _lock_active_subagent_presets(self, agents: ResolvedAgentsConfig) -> None:
        """Lock active child presets before saving head subagent bindings."""
        preset_ids = {subagent.preset_id for subagent in agents.subagents}
        if not preset_ids:
            return

        stmt = (
            select(AgentPreset.id)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id.in_(preset_ids),
                AgentPreset.deleted_at.is_(None),
            )
            # Deterministic lock order prevents ABBA deadlocks between
            # concurrent saves whose subagent sets overlap.
            .order_by(AgentPreset.id)
            .with_for_update()
        )
        active_ids = set((await self.session.execute(stmt)).scalars().all())
        if missing_ids := preset_ids - active_ids:
            missing_refs = sorted(
                {
                    subagent.preset
                    for subagent in agents.subagents
                    if subagent.preset_id in missing_ids
                }
            )
            raise TracecatValidationError(
                "Cannot save preset because it references soft-deleted or missing "
                f"subagent presets: {missing_refs}"
            )

    async def resolve_mcp_integrations(
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
                        stdio_env = await self._resolve_stdio_env(
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
                    "id": str(mcp_integration.id),
                }
                if mcp_integration.stdio_args:
                    command_config["args"] = mcp_integration.stdio_args
                if stdio_env:
                    command_config["env"] = stdio_env
                if mcp_integration.timeout:
                    command_config["timeout"] = mcp_integration.timeout

                mcp_servers.append(command_config)
                continue

            # Handle HTTP-type servers (default)
            try:
                http_config = await integrations_service.resolve_mcp_http_server_config(
                    mcp_integration
                )
            except MCPConfigurationError as e:
                logger.warning(
                    "Failed to resolve HTTP MCP integration %r, skipping: %s",
                    mcp_integration.name,
                    str(e),
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration.id),
                    },
                )
                continue
            mcp_servers.append(http_config)

        if not mcp_servers:
            raise TracecatValidationError(
                "No matching MCP integrations found for this preset in the workspace"
            )

        return mcp_servers

    async def resolve_mcp_integration_refs(
        self, mcp_integrations: list[str] | None
    ) -> list[MCPServerConfig] | None:
        """Resolve MCP integration IDs into ``MCPServerConfig``s without secrets.

        Returns metadata only — ``headers`` and stdio ``env`` are intentionally
        omitted. Each config carries its source ``id`` so trusted callers can
        re-resolve secrets per use via :meth:`resolve_mcp_integration_secrets`.

        Safe to serialize across Temporal boundaries.

        Returns ``None`` when ``mcp_integrations`` is empty. Raises
        ``TracecatValidationError`` when at least one integration was
        requested but none could be resolved — mirrors the behavior of
        :meth:`resolve_mcp_integrations`.
        """
        if not mcp_integrations:
            return None

        integrations_service = IntegrationService(self.session, role=self.role)
        available = await integrations_service.list_mcp_integrations()
        by_id = {integration.id: integration for integration in available}

        refs: list[MCPServerConfig] = []
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

            mcp_integration = by_id.get(mcp_integration_id)
            if mcp_integration is None:
                logger.warning(
                    "MCP integration not found, skipping: %r",
                    mcp_integration_id,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration_id),
                    },
                )
                continue

            if mcp_integration.server_type == "stdio":
                if not mcp_integration.stdio_command:
                    logger.warning(
                        "Stdio MCP integration %r has no stdio_command, skipping",
                        mcp_integration.name,
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue
                # Re-validate command policy at resolution time so rows that
                # predate tighter rules are rejected here rather than at spawn.
                try:
                    validate_mcp_command_config(
                        command=mcp_integration.stdio_command,
                        args=mcp_integration.stdio_args,
                        env=None,
                        name=mcp_integration.slug,
                    )
                except MCPValidationError as e:
                    logger.warning(
                        "Stdio MCP integration %r failed command validation, skipping: %s",
                        mcp_integration.name,
                        str(e),
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue
                stdio_ref: MCPServerConfig = {
                    "type": "stdio",
                    "name": mcp_integration.slug,
                    "command": mcp_integration.stdio_command,
                    "id": str(mcp_integration.id),
                }
                if mcp_integration.stdio_args:
                    stdio_ref["args"] = mcp_integration.stdio_args
                if mcp_integration.timeout is not None:
                    stdio_ref["timeout"] = mcp_integration.timeout
                refs.append(stdio_ref)
                continue

            if not mcp_integration.server_uri:
                logger.warning(
                    "HTTP MCP integration %r has no server_uri, skipping",
                    mcp_integration.name,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration.id),
                    },
                )
                continue
            http_ref: MCPHttpServerConfig = {
                "type": "http",
                "name": mcp_integration.name,
                "url": mcp_integration.server_uri,
                "id": str(mcp_integration.id),
            }
            if mcp_integration.timeout is not None:
                http_ref["timeout"] = mcp_integration.timeout
            refs.append(http_ref)

        if not refs:
            raise TracecatValidationError(
                "No matching MCP integrations found in the workspace"
            )

        return refs

    async def resolve_mcp_integration_tool_policies(
        self, mcp_integration_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, MCPToolSummary]]:
        """Resolve stored MCP tool policy by integration id and tool name."""
        if not mcp_integration_ids:
            return {}

        requested_ids = set(mcp_integration_ids)
        integrations_service = IntegrationService(self.session, role=self.role)
        available = await integrations_service.list_mcp_integrations()

        policies: dict[uuid.UUID, dict[str, MCPToolSummary]] = {}
        for mcp_integration in available:
            if mcp_integration.id not in requested_ids:
                continue
            tools = MCPToolSummary.validate_stored(
                mcp_integration.tools,
                mcp_integration_id=mcp_integration.id,
            )
            if tools is None:
                continue
            policies[mcp_integration.id] = {tool.name: tool for tool in tools}

        return policies

    async def resolve_mcp_integration_secrets(
        self, mcp_integration_id: uuid.UUID
    ) -> dict[str, str] | None:
        """Resolve the secret material for a single MCP integration.

        Returns the headers/env dict (depending on server type) freshly
        decrypted, with OAuth tokens refreshed if applicable. Returns
        ``None`` only when the integration is not found or has no secrets to
        resolve.

        Call this at the trusted edge per use — never propagate the result
        across a Temporal boundary.

        Raises:
            MCPSecretResolutionError: If configured credentials/env exist but
                cannot be resolved.
        """
        integrations_service = IntegrationService(self.session, role=self.role)
        available = await integrations_service.list_mcp_integrations()
        mcp_integration = next(
            (i for i in available if i.id == mcp_integration_id), None
        )
        if mcp_integration is None:
            logger.warning(
                "MCP integration not found",
                extra={
                    "workspace_id": str(self.workspace_id),
                    "mcp_integration_id": str(mcp_integration_id),
                },
            )
            return None

        if mcp_integration.server_type == "stdio":
            stdio_env = integrations_service.decrypt_stdio_env(mcp_integration)
            if not stdio_env:
                return None
            try:
                return await self._resolve_stdio_env(
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
                    },
                )
                raise MCPSecretResolutionError(
                    "Stdio MCP integration env could not be resolved",
                    mcp_integration_id=mcp_integration.id,
                    server_name=mcp_integration.name,
                    server_slug=mcp_integration.slug,
                ) from e

        # HTTP server — resolve headers per auth type.
        try:
            server_config = await integrations_service.resolve_mcp_http_server_config(
                mcp_integration
            )
        except MCPConfigurationError as e:
            logger.warning(
                "Failed to resolve secrets for HTTP MCP integration %r: %s",
                mcp_integration.name,
                str(e),
                extra={
                    "workspace_id": str(self.workspace_id),
                    "mcp_integration_id": str(mcp_integration.id),
                },
            )
            if mcp_integration.auth_type in {MCPAuthType.OAUTH2, MCPAuthType.CUSTOM}:
                raise MCPSecretResolutionError(
                    "HTTP MCP integration credentials could not be resolved",
                    mcp_integration_id=mcp_integration.id,
                    server_name=mcp_integration.name,
                    server_slug=mcp_integration.slug,
                ) from e
            return None
        return server_config.get("headers", {})

    async def _resolve_stdio_env(
        self,
        *,
        stdio_env: dict[str, str],
        mcp_integration_id: uuid.UUID,
        mcp_integration_slug: str,
    ) -> dict[str, str]:
        """Resolve template expressions in stdio_env using workspace secrets/vars."""
        collected = collect_expressions(stdio_env)
        if not collected.secrets and not collected.variables:
            return stdio_env

        secrets = await secrets_manager.get_action_secrets(
            secret_exprs=collected.secrets,
            action_secrets=set(),
        )
        vars_map = await get_workspace_variables(
            variable_exprs=collected.variables,
            role=self.role,
        )

        context = create_default_execution_context()
        context["SECRETS"] = secrets
        context["VARS"] = vars_map

        resolved = eval_templated_object(stdio_env, operand=context)
        if not isinstance(resolved, dict):
            raise TracecatValidationError(
                "Resolved stdio_env must be a JSON object with string values"
            )

        non_string_keys = [
            key for key, value in resolved.items() if not isinstance(value, str)
        ]
        if non_string_keys:
            raise TracecatValidationError(
                "Resolved stdio_env values must be strings "
                f"(invalid keys: {sorted(non_string_keys)})"
            )

        logger.info(
            "Resolved stdio_env template expressions",
            workspace_id=str(self.workspace_id),
            mcp_integration_id=str(mcp_integration_id),
            mcp_integration_slug=mcp_integration_slug,
            env_key_count=len(resolved),
            secret_ref_count=len(collected.secrets),
            var_ref_count=len(collected.variables),
        )

        return cast(dict[str, str], resolved)

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
            AgentPreset.deleted_at.is_(None),
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
    async def get_preset(
        self, preset_id: uuid.UUID, *, include_deleted: bool = False
    ) -> AgentPreset | None:
        """Get an agent preset by ID with proper error handling."""
        predicates = [
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.id == preset_id,
        ]
        if not include_deleted:
            predicates.append(AgentPreset.deleted_at.is_(None))
        stmt = select(AgentPreset).where(*predicates)
        if include_deleted:
            stmt = with_deleted(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_preset_by_slug(
        self, slug: str, *, include_deleted: bool = False
    ) -> AgentPreset | None:
        """Get an agent preset by slug with proper error handling."""
        predicates = [
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.slug == slug,
        ]
        if not include_deleted:
            predicates.append(AgentPreset.deleted_at.is_(None))
        stmt = select(AgentPreset).where(*predicates)
        if include_deleted:
            stmt = with_deleted(stmt)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_versions(
        self,
        preset_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[AgentPresetVersionReadMinimal]:
        """List immutable preset version metadata ordered newest first."""
        paginator = BaseCursorPaginator(self.session)
        stmt = select(
            AgentPresetVersion.id,
            AgentPresetVersion.preset_id,
            AgentPresetVersion.workspace_id,
            AgentPresetVersion.version,
            AgentPresetVersion.agents,
            AgentPresetVersion.tool_approvals,
            AgentPresetVersion.enable_internet_access,
            AgentPresetVersion.created_at,
            AgentPresetVersion.updated_at,
        ).where(
            AgentPresetVersion.workspace_id == self.workspace_id,
            AgentPresetVersion.preset_id == preset_id,
        )
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError(
                    "Invalid cursor for agent preset versions"
                ) from err
            cursor_version = cursor_data.sort_value
            if not isinstance(cursor_version, int):
                raise TracecatValidationError(
                    "Invalid cursor for agent preset versions"
                )
            cursor_predicate = sa.or_(
                AgentPresetVersion.version > cursor_version,
                sa.and_(
                    AgentPresetVersion.version == cursor_version,
                    AgentPresetVersion.id > cursor_id,
                ),
            )
            if not params.reverse:
                cursor_predicate = sa.or_(
                    AgentPresetVersion.version < cursor_version,
                    sa.and_(
                        AgentPresetVersion.version == cursor_version,
                        AgentPresetVersion.id < cursor_id,
                    ),
                )
            stmt = stmt.where(cursor_predicate)

        if params.reverse:
            stmt = stmt.order_by(
                AgentPresetVersion.version.asc(),
                AgentPresetVersion.id.asc(),
            )
        else:
            stmt = stmt.order_by(
                AgentPresetVersion.version.desc(),
                AgentPresetVersion.id.desc(),
            )
        stmt = stmt.limit(params.limit + 1)
        result = await self.session.execute(stmt)
        versions = [
            AgentPresetVersionReadMinimal(
                id=version_id,
                preset_id=row_preset_id,
                workspace_id=workspace_id,
                version=version_number,
                capabilities=_agent_preset_capabilities(
                    agents_config=agents,
                    tool_approvals=tool_approvals,
                    enable_internet_access=enable_internet_access,
                ),
                subagent_eligibility=build_subagent_eligibility(
                    agents_config=agents,
                    tool_approvals=tool_approvals,
                ),
                created_at=created_at,
                updated_at=updated_at,
            )
            for (
                version_id,
                row_preset_id,
                workspace_id,
                version_number,
                agents,
                tool_approvals,
                enable_internet_access,
                created_at,
                updated_at,
            ) in result.tuples().all()
        ]
        has_more = len(versions) > params.limit
        items = versions[: params.limit]

        next_cursor = None
        if has_more and items:
            last_version = items[-1]
            next_cursor = paginator.encode_cursor(
                last_version.id,
                sort_column="version",
                sort_value=last_version.version,
            )

        prev_cursor = None
        if params.cursor and items:
            first_version = items[0]
            prev_cursor = paginator.encode_cursor(
                first_version.id,
                sort_column="version",
                sort_value=first_version.version,
            )

        return CursorPaginatedResponse(
            items=list(items),
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=params.cursor is not None,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version(self, version_id: uuid.UUID) -> AgentPresetVersion | None:
        """Get a preset version by ID."""
        stmt = select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == self.workspace_id,
            AgentPresetVersion.id == version_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_active_version(
        self,
        *,
        version_id: uuid.UUID,
        preset_id: uuid.UUID | None = None,
    ) -> AgentPresetVersion | None:
        """Get a preset version only when its parent preset is active.

        ``preset_id`` additionally scopes the version to that preset.
        """
        stmt = (
            select(AgentPresetVersion)
            .join(AgentPreset, AgentPresetVersion.preset_id == AgentPreset.id)
            .where(
                AgentPresetVersion.workspace_id == self.workspace_id,
                AgentPresetVersion.id == version_id,
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.deleted_at.is_(None),
            )
        )
        if preset_id is not None:
            stmt = stmt.where(AgentPresetVersion.preset_id == preset_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version_by_number(
        self, *, preset_id: uuid.UUID, version: int
    ) -> AgentPresetVersion | None:
        """Get a preset version by logical preset and version number."""
        stmt = select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == self.workspace_id,
            AgentPresetVersion.preset_id == preset_id,
            AgentPresetVersion.version == version,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def _binding_specs_from_inputs(
        self,
        bindings: Sequence[AgentPresetSkillBindingBase],
        *,
        for_update: bool = False,
    ) -> list[SkillBindingSpec]:
        """Normalize authored Skill head bindings to current versions."""

        return await self._current_skill_binding_specs(
            [binding.skill_id for binding in bindings],
            for_update=for_update,
        )

    async def _get_head_skill_binding_specs(
        self, preset_id: uuid.UUID
    ) -> list[SkillBindingSpec]:
        """Return the current head bindings for a preset."""

        stmt = select(
            AgentPresetSkill.skill_id,
            AgentPresetSkill.skill_version_id,
        ).where(
            AgentPresetSkill.workspace_id == self.workspace_id,
            AgentPresetSkill.preset_id == preset_id,
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        return sorted(
            SkillBindingSpec(skill_id, skill_version_id)
            for skill_id, skill_version_id in rows
        )

    async def _current_skill_binding_specs(
        self, skill_ids: Sequence[uuid.UUID], *, for_update: bool = False
    ) -> list[SkillBindingSpec]:
        """Return bindable Skill head IDs with their current versions."""

        if not skill_ids:
            return []
        if len(set(skill_ids)) != len(skill_ids):
            raise TracecatValidationError(
                "Duplicate skills are not allowed on a preset",
                detail={"code": "duplicate_skill_binding"},
            )

        normalized_ids = sorted(set(skill_ids), key=str)
        stmt = select(Skill).where(
            Skill.workspace_id == self.workspace_id,
            Skill.id.in_(normalized_ids),
            Skill.deleted_at.is_(None),
            Skill.archived_at.is_(None),
        )
        if for_update:
            stmt = stmt.order_by(Skill.id).with_for_update()
        skills = {
            skill.id: skill
            for skill in (await self.session.execute(stmt)).scalars().all()
        }
        missing = [str(skill_id) for skill_id in skill_ids if skill_id not in skills]
        if missing:
            raise TracecatValidationError(
                f"Some skills were not found in this workspace: {sorted(missing)}",
                detail={"code": "skill_not_found", "missing_skill_ids": missing},
            )

        specs: list[SkillBindingSpec] = []
        for skill_id in skill_ids:
            skill = skills[skill_id]
            if skill.current_version_id is None:
                raise TracecatValidationError(
                    f"Skill '{skill.name}' has no published version",
                    detail={"code": "skill_not_published", "skill_id": str(skill.id)},
                )
            specs.append(SkillBindingSpec(skill_id, skill.current_version_id))
        return sorted(specs)

    async def _resolve_head_skill_binding_specs(
        self, preset_id: uuid.UUID, *, for_update: bool = False
    ) -> list[SkillBindingSpec]:
        """Resolve a preset's Skill head edges to current immutable versions."""

        stmt = select(AgentPresetSkill.skill_id).where(
            AgentPresetSkill.workspace_id == self.workspace_id,
            AgentPresetSkill.preset_id == preset_id,
        )
        skill_ids = (await self.session.execute(stmt)).scalars().all()
        return await self._current_skill_binding_specs(
            list(skill_ids),
            for_update=for_update,
        )

    async def _validate_unique_skill_binding_names(
        self,
        binding_specs: Sequence[SkillBindingSpec],
        *,
        preset_id: uuid.UUID,
    ) -> None:
        """Reject duplicate names in one exact resolved Skill binding set."""

        if not binding_specs:
            return
        duplicate_name_stmt = (
            select(
                SkillVersion.name,
                func.count(SkillVersion.id).label("binding_count"),
            )
            .where(
                SkillVersion.workspace_id == self.workspace_id,
                SkillVersion.id.in_(
                    [binding.skill_version_id for binding in binding_specs]
                ),
            )
            .group_by(SkillVersion.name)
            .having(func.count(SkillVersion.id) > 1)
        )
        duplicate_names = sorted(
            name
            for name, _count in (await self.session.execute(duplicate_name_stmt))
            .tuples()
            .all()
            if name is not None
        )
        if duplicate_names:
            raise TracecatValidationError(
                "Agent preset version cannot include duplicate skill names",
                detail={
                    "code": "duplicate_skill_names",
                    "skill_names": duplicate_names,
                    "preset_id": str(preset_id),
                },
            )

    async def _replace_head_skill_bindings(
        self,
        preset_id: uuid.UUID,
        bindings: Sequence[AgentPresetSkillBindingBase],
        *,
        binding_specs: Sequence[SkillBindingSpec] | None = None,
    ) -> None:
        """Replace the mutable head skill bindings for a preset."""

        specs = (
            list(binding_specs)
            if binding_specs is not None
            else await self._binding_specs_from_inputs(bindings, for_update=True)
        )
        await self.session.execute(
            sa.delete(AgentPresetSkill).where(
                AgentPresetSkill.workspace_id == self.workspace_id,
                AgentPresetSkill.preset_id == preset_id,
            )
        )
        for binding in specs:
            self.session.add(
                AgentPresetSkill(
                    workspace_id=self.workspace_id,
                    preset_id=preset_id,
                    skill_id=binding.skill_id,
                    skill_version_id=binding.skill_version_id,
                )
            )
        await self.session.flush()

    async def _snapshot_version_skill_bindings(
        self,
        preset_id: uuid.UUID,
        preset_version_id: uuid.UUID,
        *,
        binding_specs: Sequence[SkillBindingSpec] | None = None,
    ) -> None:
        """Write current Skill versions into an immutable preset snapshot."""

        specs = (
            list(binding_specs)
            if binding_specs is not None
            else await self._resolve_head_skill_binding_specs(
                preset_id,
                for_update=True,
            )
        )
        for binding in specs:
            await self.session.execute(
                sa.update(AgentPresetSkill)
                .where(
                    AgentPresetSkill.workspace_id == self.workspace_id,
                    AgentPresetSkill.preset_id == preset_id,
                    AgentPresetSkill.skill_id == binding.skill_id,
                )
                .values(skill_version_id=binding.skill_version_id)
            )
            self.session.add(
                AgentPresetVersionSkill(
                    workspace_id=self.workspace_id,
                    preset_version_id=preset_version_id,
                    skill_id=binding.skill_id,
                    skill_version_id=binding.skill_version_id,
                )
            )
        await self.session.flush()

    async def _restore_head_skill_bindings_from_version(
        self, *, preset_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        """Copy exact historical skill versions back to the mutable preset head."""

        stmt = select(
            AgentPresetVersionSkill.skill_id,
            AgentPresetVersionSkill.skill_version_id,
        ).where(
            AgentPresetVersionSkill.workspace_id == self.workspace_id,
            AgentPresetVersionSkill.preset_version_id == version_id,
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        await self.skills.validate_binding_inputs(
            [
                AgentPresetSkillBindingBase(skill_id=skill_id)
                for skill_id, _skill_version_id in rows
            ],
            for_update=True,
        )
        await self.session.execute(
            sa.delete(AgentPresetSkill).where(
                AgentPresetSkill.workspace_id == self.workspace_id,
                AgentPresetSkill.preset_id == preset_id,
            )
        )
        for skill_id, skill_version_id in rows:
            self.session.add(
                AgentPresetSkill(
                    workspace_id=self.workspace_id,
                    preset_id=preset_id,
                    skill_id=skill_id,
                    skill_version_id=skill_version_id,
                )
            )
        await self.session.flush()

    async def _compare_version_skill_bindings(
        self, base_version_id: uuid.UUID, compare_version_id: uuid.UUID
    ) -> list[AgentPresetSkillBindingChange]:
        """Return a diff of exact skill version refs between preset versions."""

        base_bindings = {
            binding.skill_id: binding
            for binding in await self._list_version_skill_bindings(base_version_id)
        }
        compare_bindings = {
            binding.skill_id: binding
            for binding in await self._list_version_skill_bindings(compare_version_id)
        }
        skill_changes: list[AgentPresetSkillBindingChange] = []
        for skill_id in sorted(set(base_bindings) | set(compare_bindings)):
            base_binding = base_bindings.get(skill_id)
            compare_binding = compare_bindings.get(skill_id)
            if (
                base_binding is not None
                and compare_binding is not None
                and base_binding.skill_version_id == compare_binding.skill_version_id
            ):
                continue
            skill_name = (
                base_binding.skill_name
                if base_binding is not None
                else compare_binding.skill_name
                if compare_binding is not None
                else str(skill_id)
            )
            skill_changes.append(
                AgentPresetSkillBindingChange(
                    skill_id=skill_id,
                    skill_name=skill_name,
                    old_skill_version_id=(
                        base_binding.skill_version_id if base_binding else None
                    ),
                    old_skill_version=base_binding.skill_version
                    if base_binding
                    else None,
                    new_skill_version_id=(
                        compare_binding.skill_version_id if compare_binding else None
                    ),
                    new_skill_version=(
                        compare_binding.skill_version if compare_binding else None
                    ),
                )
            )
        return skill_changes

    async def _lock_preset_row(self, preset_id: uuid.UUID) -> None:
        """Serialize preset mutations using a row-level lock."""
        stmt = (
            select(AgentPreset.id)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id == preset_id,
                AgentPreset.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is None:
            raise TracecatNotFoundError(f"Agent preset '{preset_id}' not found")

    async def get_current_version_for_preset(
        self, preset: AgentPreset
    ) -> AgentPresetVersion:
        """Return the current version for a preset."""
        if (
            preset.current_version_id is not None
            and (version := await self.get_version(preset.current_version_id))
            is not None
        ):
            return version

        stmt = (
            select(AgentPresetVersion)
            .where(
                AgentPresetVersion.workspace_id == self.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
            )
            .order_by(
                AgentPresetVersion.version.desc(),
                AgentPresetVersion.created_at.desc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        if version := result.scalars().first():
            return version
        raise TracecatNotFoundError(
            f"Agent preset version for preset '{preset.id}' not found"
        )

    @require_scope("agent:update")
    @audit_log(resource_type="agent_preset", action="update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def restore_version(
        self, preset: AgentPreset, version: AgentPresetVersion
    ) -> AgentPreset:
        """Restore a historical version as the current preset head."""
        if version.preset_id != preset.id:
            raise TracecatValidationError(
                "Preset version does not belong to the selected preset"
            )

        await self._lock_preset_row(preset.id)
        restored_agents = await self._resolve_restored_agents_config(preset, version)
        self._sync_preset_head_from_version(
            preset,
            version,
            agents=restored_agents,
        )
        await self._restore_head_skill_bindings_from_version(
            preset_id=preset.id,
            version_id=version.id,
        )
        preset.current_version_id = version.id
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    async def _resolve_restored_agents_config(
        self,
        preset: AgentPreset,
        version: AgentPresetVersion,
    ) -> dict[str, Any]:
        """Resolve historical agents config before making it active again."""
        agents = await self._resolve_preset_subagent_configs(
            version.agents,
            parent_preset_id=preset.id,
            parent_slug=preset.slug,
        )
        return agents

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def compare_versions(
        self,
        base_version: AgentPresetVersion,
        compare_version: AgentPresetVersion,
    ) -> AgentPresetVersionDiff:
        """Return a structured diff between two preset versions."""
        if base_version.preset_id != compare_version.preset_id:
            raise TracecatValidationError("Can only compare versions for one preset")

        scalar_changes: list[ScalarFieldChange] = []
        for field in (
            "model_name",
            "model_provider",
            "catalog_id",
            "base_url",
            "output_type",
            "retries",
            "enable_thinking",
            "enable_internet_access",
            "agents",
        ):
            old_value = getattr(base_version, field)
            new_value = getattr(compare_version, field)
            if old_value != new_value:
                scalar_changes.append(
                    ScalarFieldChange(
                        field=field,
                        old_value=old_value,
                        new_value=new_value,
                    )
                )

        list_changes: list[StringListFieldChange] = []
        for field in ("actions", "namespaces", "mcp_integrations"):
            base_values = set(getattr(base_version, field) or [])
            compare_values = set(getattr(compare_version, field) or [])
            added = sorted(compare_values - base_values)
            removed = sorted(base_values - compare_values)
            if added or removed:
                list_changes.append(
                    StringListFieldChange(
                        field=field,
                        added=added,
                        removed=removed,
                    )
                )

        tool_approval_changes: list[ToolApprovalFieldChange] = []
        base_approvals = base_version.tool_approvals or {}
        compare_approvals = compare_version.tool_approvals or {}
        for tool in sorted(set(base_approvals) | set(compare_approvals)):
            old_value = base_approvals.get(tool)
            new_value = compare_approvals.get(tool)
            if old_value != new_value:
                tool_approval_changes.append(
                    ToolApprovalFieldChange(
                        tool=tool,
                        old_value=old_value,
                        new_value=new_value,
                    )
                )

        skill_changes = await self._compare_version_skill_bindings(
            base_version.id,
            compare_version.id,
        )
        instructions_changed = base_version.instructions != compare_version.instructions
        total_changes = (
            int(instructions_changed)
            + len(scalar_changes)
            + len(list_changes)
            + len(tool_approval_changes)
            + len(skill_changes)
        )

        return AgentPresetVersionDiff(
            base_version_id=base_version.id,
            base_version=base_version.version,
            compare_version_id=compare_version.id,
            compare_version=compare_version.version,
            instructions_changed=instructions_changed,
            base_instructions=base_version.instructions,
            compare_instructions=compare_version.instructions,
            scalar_changes=scalar_changes,
            list_changes=list_changes,
            tool_approval_changes=tool_approval_changes,
            skill_changes=skill_changes,
            total_changes=total_changes,
        )

    async def _version_to_agent_config(
        self, version: AgentPresetVersion
    ) -> AgentConfig:
        use_latest_resource_versions = await self.use_latest_resource_versions()
        # Resolve refs only — no headers / stdio env. The resulting
        # AgentConfig is safe to cross Temporal boundaries. Trusted callers
        # (build_tool_definitions, trusted MCP server) re-resolve secrets
        # per use via resolve_mcp_integration_secrets.
        mcp_servers = await self.resolve_mcp_integration_refs(version.mcp_integrations)
        model_settings: dict[str, Any] = {}
        resolved_skills = await self.skills.get_resolved_skill_refs_for_preset_version(
            version.id,
            use_latest_versions=use_latest_resource_versions,
        )
        duplicate_skill_names = sorted(
            name
            for name, count in Counter(
                resolved_skill.skill_name for resolved_skill in resolved_skills
            ).items()
            if count > 1
        )
        if duplicate_skill_names:
            raise TracecatValidationError(
                "Resolved preset version contains duplicate skill names",
                detail={
                    "code": "duplicate_skill_names",
                    "skill_names": duplicate_skill_names,
                    "preset_version_id": str(version.id),
                },
            )
        # Only disable parallel tool calls if tools will be present
        if version.actions or mcp_servers:
            model_settings["parallel_tool_calls"] = False
        agents = AgentSubagentsConfig.model_validate(version.agents)
        if use_latest_resource_versions:
            resolved_agents = await resolve_agents_config(
                self,
                agents=agents,
                parent_preset_id=version.preset_id,
                include_runtime_config=False,
                follow_latest_versions=True,
            )
            binding = resolved_agents.to_agents_binding()
            agents = AgentSubagentsConfig(
                enabled=binding.enabled,
                subagents=list(binding.subagents),
            )
        return AgentConfig(
            model_name=version.model_name,
            model_provider=version.model_provider,
            catalog_id=version.catalog_id,
            base_url=version.base_url,
            instructions=version.instructions,
            output_type=cast(OutputType | None, version.output_type),
            actions=version.actions,
            namespaces=version.namespaces,
            tool_approvals=version.tool_approvals,
            mcp_servers=mcp_servers,
            agents=agents,
            retries=version.retries,
            model_settings=model_settings,
            enable_thinking=version.enable_thinking,
            enable_internet_access=version.enable_internet_access,
            resolved_skills=resolved_skills,
        )

    async def _create_version_from_preset(
        self, preset: AgentPreset, *, preset_locked: bool = False
    ) -> AgentPresetVersion:
        """Create and flush a new immutable version from the preset head."""
        if not preset_locked:
            await self._lock_preset_row(preset.id)
        binding_specs = await self._resolve_head_skill_binding_specs(
            preset.id,
            for_update=True,
        )
        await self._validate_unique_skill_binding_names(
            binding_specs,
            preset_id=preset.id,
        )
        stmt = (
            select(AgentPresetVersion.version)
            .where(
                AgentPresetVersion.workspace_id == self.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
            )
            .order_by(AgentPresetVersion.version.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        current_version = result.scalar_one_or_none()
        next_version = (current_version or 0) + 1

        version = AgentPresetVersion(
            workspace_id=self.workspace_id,
            preset_id=preset.id,
            version=next_version,
            instructions=preset.instructions,
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            catalog_id=preset.catalog_id,
            base_url=preset.base_url,
            output_type=preset.output_type,
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            mcp_integrations=preset.mcp_integrations,
            agents=preset.agents,
            retries=preset.retries,
            enable_thinking=preset.enable_thinking,
            enable_internet_access=preset.enable_internet_access,
        )
        self.session.add(version)
        await self.session.flush()
        await self._snapshot_version_skill_bindings(
            preset.id,
            version.id,
            binding_specs=binding_specs,
        )
        return version

    def _sync_preset_head_from_version(
        self,
        preset: AgentPreset,
        version: AgentPresetVersion,
        *,
        agents: dict[str, Any],
    ) -> None:
        """Copy versioned execution fields onto the mutable preset head."""
        preset.instructions = version.instructions
        preset.model_name = version.model_name
        preset.model_provider = version.model_provider
        preset.catalog_id = version.catalog_id
        preset.base_url = version.base_url
        preset.output_type = version.output_type
        preset.actions = version.actions
        preset.namespaces = version.namespaces
        preset.tool_approvals = version.tool_approvals
        preset.mcp_integrations = version.mcp_integrations
        preset.agents = agents
        preset.retries = version.retries
        preset.enable_thinking = version.enable_thinking
        preset.enable_internet_access = version.enable_internet_access
