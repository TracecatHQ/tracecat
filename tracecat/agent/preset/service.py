"""Service layer for managing agent presets."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

import sqlalchemy as sa
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import selectinload

from tracecat.agent.access.service import AgentModelAccessService
from tracecat.agent.channels.service import AgentChannelService
from tracecat.agent.common.types import (
    MCPHttpServerConfig,
    MCPServerConfig,
    MCPServerToolSummary,
    MCPStdioServerConfig,
)
from tracecat.agent.preset.resolved_refs import (
    ResolvedRef,
    ResolvedRefs,
    merge_resolved_refs,
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsConfigResult,
    SkippedAgentPresetRef,
    resolve_agents_config,
)
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetExecutionConfigWrite,
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
from tracecat.agent.skill.service import SkillService
from tracecat.agent.skill.types import ResolvedSkillRefsResult
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    HeadAttachedSubagentRef,
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
    AgentPresetVersionSubagent,
    MCPIntegration,
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

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_presets(self) -> Sequence[AgentPreset]:
        """Return all agent presets for the current workspace ordered by recency."""

        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == self.workspace_id)
            .where(AgentPreset.deleted_at.is_(None))
            .order_by(AgentPreset.created_at.desc())
            .options(
                selectinload(AgentPreset.tags),
                selectinload(AgentPreset.current_version),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def _list_version_skill_bindings(
        self, version_id: uuid.UUID
    ) -> list[AgentPresetSkillBindingRead]:
        """Return Skill ResourceHead edges for an immutable preset version."""

        stmt = (
            select(AgentPresetVersionSkill.skill_id, Skill.name)
            .join(Skill, AgentPresetVersionSkill.skill_id == Skill.id)
            .where(
                AgentPresetVersionSkill.workspace_id == self.workspace_id,
                AgentPresetVersionSkill.preset_version_id == version_id,
            )
            .order_by(Skill.name.asc(), AgentPresetVersionSkill.skill_id.asc())
        )
        rows = (await self.session.execute(with_deleted(stmt))).tuples().all()
        return [
            AgentPresetSkillBindingRead(skill_id=skill_id, skill_name=skill_name)
            for skill_id, skill_name in rows
        ]

    async def _get_version_agents_config(
        self, version: AgentPresetVersion
    ) -> AgentSubagentsConfig:
        """Load snapshotted subagent ResourceHead edges for a preset version."""

        # NULL is the old-writer epoch. During the rolling expand deployment,
        # an older task can still create a JSON-only version after the schema
        # migration has completed.
        if version.subagents_enabled is None:
            return AgentSubagentsConfig.model_validate(version.agents)

        stmt = (
            select(
                AgentPreset.id,
                AgentPreset.slug,
                AgentPresetVersionSubagent.alias,
                AgentPresetVersionSubagent.description,
                AgentPresetVersionSubagent.max_turns,
            )
            .select_from(AgentPresetVersionSubagent)
            .join(
                AgentPreset,
                sa.and_(
                    AgentPreset.workspace_id == AgentPresetVersionSubagent.workspace_id,
                    AgentPreset.id == AgentPresetVersionSubagent.child_preset_id,
                ),
            )
            .where(
                AgentPresetVersionSubagent.workspace_id == self.workspace_id,
                AgentPresetVersionSubagent.parent_preset_version_id == version.id,
            )
            .order_by(AgentPresetVersionSubagent.alias)
        )
        rows = (await self.session.execute(with_deleted(stmt))).tuples().all()
        return AgentSubagentsConfig(
            enabled=version.subagents_enabled,
            subagents=[
                HeadAttachedSubagentRef(
                    preset=child_slug,
                    preset_id=child_id,
                    name=alias,
                    description=description,
                    max_turns=max_turns,
                )
                for child_id, child_slug, alias, description, max_turns in rows
            ],
        )

    async def get_version_subagent_binding(
        self, version: AgentPresetVersion
    ) -> ResolvedAgentsConfig:
        """Resolve a preset version's snapshotted ResourceHead edges."""

        resolved = await self._resolve_version_subagents(version)
        return resolved.to_agents_binding()

    async def _resolve_version_subagents(
        self, version: AgentPresetVersion
    ) -> ResolvedAgentsConfigResult:
        """Resolve a preset version's snapshotted ResourceHead edges and refs."""

        return await resolve_agents_config(
            self,
            agents=await self._get_version_agents_config(version),
            parent_preset_id=version.preset_id,
            include_runtime_config=False,
        )

    async def build_preset_read(self, preset: AgentPreset) -> AgentPresetRead:
        """Build the response model for a preset."""

        metadata: dict[str, Any] = {
            "id": preset.id,
            "workspace_id": preset.workspace_id,
            "name": preset.name,
            "slug": preset.slug,
            "description": preset.description,
            "folder_id": preset.folder_id,
            "created_at": preset.created_at,
            "updated_at": preset.updated_at,
        }
        if preset.current_version_id is None:
            return AgentPresetRead(
                **metadata,
                current_version_id=None,
                model_name="",
                model_provider="",
            )
        version = await self.get_current_version_for_preset(preset)
        agents = await self._get_version_agents_config(version)
        return AgentPresetRead(
            **metadata,
            current_version_id=version.id,
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
            skills=await self._list_version_skill_bindings(version.id),
        )

    async def build_version_read(
        self, version: AgentPresetVersion
    ) -> AgentPresetVersionRead:
        """Build the response model for an immutable preset version."""

        agents = await self._get_version_agents_config(version)
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
        requires_internet_access = await self._has_stdio_mcp_integration(
            params.mcp_integrations
        )
        catalog_entry: AgentCatalog | None = None
        if params.catalog_id is not None:
            catalog_entry = await self._get_enabled_catalog_entry(params.catalog_id)
        preset = AgentPreset(
            workspace_id=self.workspace_id,
            slug=slug,
            name=params.name,
            description=params.description,
        )
        self.session.add(preset)
        await self.session.flush()
        execution_data = params.model_dump(include=self.EXECUTION_FIELDS)
        if catalog_entry is not None:
            execution_data["model_name"] = catalog_entry.model_name
            execution_data["model_provider"] = catalog_entry.model_provider
        if requires_internet_access:
            execution_data["enable_internet_access"] = True
        execution = AgentPresetExecutionConfigWrite.model_validate(execution_data)
        agents = await self._resolve_preset_subagent_configs(
            params.agents,
            parent_preset_id=preset.id,
            parent_slug=slug,
        )
        skill_ids = await self._binding_ids_from_inputs(
            params.skills or [],
            for_update=True,
        )
        version = await self._create_version(
            preset,
            config=execution,
            skill_ids=skill_ids,
            agents=agents,
        )
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

        preset = await self._lock_preset_row(preset.id)
        current_version = (
            await self.get_current_version_for_preset(preset)
            if preset.current_version_id is not None
            else None
        )
        current_agents = (
            await self._get_version_agents_config(current_version)
            if current_version is not None
            else AgentSubagentsConfig()
        )
        current_skill_ids = (
            await self._get_version_skill_binding_ids(current_version.id)
            if current_version is not None
            else []
        )

        fields = params.model_dump(exclude_unset=True)
        if "name" in fields:
            preset.name = fields.pop("name")
        if "slug" in fields:
            preset.slug = await self._normalize_and_validate_slug(
                proposed_slug=fields.pop("slug"),
                fallback_name=preset.name,
                exclude_id=preset.id,
            )
        if "description" in fields:
            preset.description = fields.pop("description")

        execution_updates = {
            field: fields.pop(field)
            for field in tuple(fields)
            if field in self.EXECUTION_FIELDS
        }
        if actions := execution_updates.get("actions"):
            await self._validate_actions(actions)
        if "mcp_integrations" in execution_updates:
            requires_internet_access = await self._has_stdio_mcp_integration(
                execution_updates["mcp_integrations"]
            )
        else:
            requires_internet_access = False
            if current_version is not None and (
                execution_updates.get("enable_internet_access") is False
                or not current_version.enable_internet_access
            ):
                requires_internet_access = await self._has_stdio_mcp_integration(
                    current_version.mcp_integrations,
                    raise_on_missing=False,
                )

        if requires_internet_access:
            execution_updates["enable_internet_access"] = True

        if "catalog_id" in execution_updates:
            effective_catalog_id = execution_updates["catalog_id"]
        elif current_version is not None and (
            "model_name" in execution_updates or "model_provider" in execution_updates
        ):
            effective_catalog_id = current_version.catalog_id
        else:
            effective_catalog_id = None
        if effective_catalog_id is not None:
            catalog_entry = await self._get_enabled_catalog_entry(effective_catalog_id)
            execution_updates["model_name"] = catalog_entry.model_name
            execution_updates["model_provider"] = catalog_entry.model_provider

        current_execution = (
            AgentPresetExecutionConfigWrite.model_validate(
                {
                    field: getattr(current_version, field)
                    for field in self.EXECUTION_FIELDS
                    if field != "agents"
                }
                | {"agents": current_agents}
            )
            if current_version is not None
            else None
        )
        agents: AgentSubagentsConfig | ResolvedAgentsConfig = current_agents
        if "agents" in execution_updates:
            agents = await self._resolve_preset_subagent_configs(
                execution_updates["agents"],
                parent_preset_id=preset.id,
                parent_slug=preset.slug,
            )
            execution_updates["agents"] = agents.model_dump(mode="python")
        skill_ids = current_skill_ids
        if "skills" in params.model_fields_set:
            skill_ids = await self._binding_ids_from_inputs(
                params.skills or [],
                for_update=True,
            )

        should_build_version = current_execution is not None or bool(
            execution_updates or "skills" in params.model_fields_set
        )
        execution = (
            AgentPresetExecutionConfigWrite.model_validate(
                (current_execution.model_dump() if current_execution else {})
                | execution_updates
            )
            if should_build_version
            else None
        )
        execution_changed = execution is not None and (
            current_execution is None
            or execution.model_dump(exclude={"agents"})
            != current_execution.model_dump(exclude={"agents"})
            or skill_ids != current_skill_ids
            or agents.enabled != current_agents.enabled
            or self._subagent_diff_value(agents)
            != self._subagent_diff_value(current_agents)
        )
        self.session.add(preset)
        if execution_changed:
            assert execution is not None
            version = await self._create_version(
                preset,
                config=execution,
                skill_ids=skill_ids,
                agents=agents,
                preset_locked=True,
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
        preset = await self._lock_preset_row(preset.id)
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
        """Count live heads whose current version references ``preset``."""

        edge_stmt = (
            select(func.count())
            .select_from(AgentPreset)
            .join(
                AgentPresetVersion,
                sa.and_(
                    AgentPresetVersion.workspace_id == AgentPreset.workspace_id,
                    AgentPresetVersion.id == AgentPreset.current_version_id,
                ),
            )
            .join(
                AgentPresetVersionSubagent,
                sa.and_(
                    AgentPresetVersionSubagent.workspace_id == AgentPreset.workspace_id,
                    AgentPresetVersionSubagent.parent_preset_version_id
                    == AgentPreset.current_version_id,
                ),
            )
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id != preset.id,
                AgentPreset.deleted_at.is_(None),
                AgentPresetVersion.subagents_enabled.is_not(None),
                AgentPresetVersionSubagent.child_preset_id == preset.id,
            )
        )
        edge_count = (await self.session.execute(edge_stmt)).scalar_one()

        legacy_subagents = AgentPresetVersion.agents["subagents"]
        legacy_subagent_array = sa.case(
            (
                sa.func.jsonb_typeof(legacy_subagents) == "array",
                legacy_subagents,
            ),
            else_=sa.literal([], type_=JSONB),
        )
        legacy_refs = sa.func.jsonb_array_elements(legacy_subagent_array).table_valued(
            "value"
        )
        legacy_ref = sa.cast(legacy_refs.c.value, JSONB)
        legacy_stmt = (
            select(func.count())
            .select_from(AgentPresetVersion)
            .join(
                AgentPreset,
                sa.and_(
                    AgentPreset.workspace_id == AgentPresetVersion.workspace_id,
                    AgentPreset.current_version_id == AgentPresetVersion.id,
                ),
            )
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id != preset.id,
                AgentPreset.deleted_at.is_(None),
                AgentPresetVersion.subagents_enabled.is_(None),
                sa.exists(
                    select(1)
                    .select_from(legacy_refs)
                    .where(
                        sa.or_(
                            legacy_ref["preset_id"].as_string() == str(preset.id),
                            sa.and_(
                                legacy_ref["preset_id"].as_string().is_(None),
                                legacy_ref["preset"].as_string() == preset.slug,
                            ),
                        )
                    )
                ),
            )
        )
        legacy_count = (await self.session.execute(legacy_stmt)).scalar_one()
        return edge_count + legacy_count

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
        include_deleted_preset: bool = False,
    ) -> AgentConfig:
        """Resolve a current head or an exact run/restore version to config."""
        version = await self.resolve_agent_preset_version(
            preset_id=preset_id,
            slug=slug,
            preset_version_id=preset_version_id,
            preset_version=preset_version,
            include_deleted_preset=include_deleted_preset,
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
        include_deleted_preset: bool = False,
    ) -> AgentPresetVersion:
        """Resolve a ResourceHead, restore input, or exact run snapshot."""
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
            version = (
                await self.get_version(preset_version_id)
                if include_deleted_preset
                else await self.get_active_version(version_id=preset_version_id)
            )
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

    async def resolve_agent_preset_version_for_subagent_ref(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
    ) -> AgentPresetVersion | SkippedAgentPresetRef:
        """Resolve a child ResourceHead to its current version or a skip record."""

        preset = await self._get_preset_for_head_resolution(
            preset_id=preset_id,
            slug=slug,
        )
        if preset is None:
            return self._skipped_agent_preset_ref(
                preset_id=preset_id,
                preset_slug=slug,
                reason="not_found",
            )
        if preset.deleted_at is not None:
            return self._skipped_agent_preset_ref(
                preset_id=preset.id,
                preset_slug=preset.slug,
                reason="deleted",
            )
        if preset.current_version_id is None:
            return self._skipped_agent_preset_ref(
                preset_id=preset.id,
                preset_slug=preset.slug,
                reason="unpublished",
            )
        version = await self.get_version(preset.current_version_id)
        if version is None or version.preset_id != preset.id:
            return self._skipped_agent_preset_ref(
                preset_id=preset.id,
                preset_slug=preset.slug,
                reason="unpublished",
            )
        return version

    async def resolve_agent_preset_version_snapshot(
        self,
        *,
        preset_version_id: uuid.UUID,
        preset_id: uuid.UUID | None = None,
        preset_slug: str | None = None,
    ) -> AgentPresetVersion | SkippedAgentPresetRef:
        """Resolve an exact version captured in an immutable run snapshot."""

        version = await self.get_version(preset_version_id)
        if version is None or (
            preset_id is not None and version.preset_id != preset_id
        ):
            return self._skipped_agent_preset_ref(
                preset_id=preset_id,
                preset_slug=preset_slug,
                reason="not_found",
            )
        return version

    def _skipped_agent_preset_ref(
        self,
        *,
        preset_id: uuid.UUID | None,
        preset_slug: str | None,
        reason: Literal["deleted", "unpublished", "not_found"],
    ) -> SkippedAgentPresetRef:
        skipped = SkippedAgentPresetRef(
            preset_id=preset_id,
            preset_slug=preset_slug,
            reason=reason,
        )
        self._log_skipped_agent_preset_ref(skipped)
        return skipped

    async def _get_preset_for_head_resolution(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
    ) -> AgentPreset | None:
        """Return a child head for resolution, including tombstones."""

        if preset_id is None and slug is None:
            raise ValueError("Either preset_id or slug must be provided")
        predicates = [AgentPreset.workspace_id == self.workspace_id]
        if preset_id is not None:
            predicates.append(AgentPreset.id == preset_id)
        else:
            predicates.append(AgentPreset.slug == slug)
        stmt = (
            select(AgentPreset)
            .where(*predicates)
            .order_by(
                AgentPreset.deleted_at.is_(None).desc(),
                AgentPreset.created_at.desc(),
            )
            .limit(1)
        )
        return (await self.session.execute(with_deleted(stmt))).scalar_one_or_none()

    async def get_successor_id_for_deleted_preset_ref(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
    ) -> uuid.UUID | None:
        """Return the live owner of a deleted preset slug, without rebinding."""

        if slug is None and preset_id is not None:
            tombstone = await self._get_preset_for_head_resolution(preset_id=preset_id)
            slug = tombstone.slug if tombstone is not None else None
        if slug is None:
            return None

        predicates = [
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.slug == slug,
            AgentPreset.deleted_at.is_(None),
        ]
        if preset_id is not None:
            predicates.append(AgentPreset.id != preset_id)
        stmt = (
            select(AgentPreset.id)
            .where(*predicates)
            .order_by(AgentPreset.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def build_root_preset_failure_refs(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> ResolvedRefs:
        """Classify a failing root preset lookup without making it non-fatal."""

        preset: AgentPreset | None = None
        if preset_version_id is not None:
            stmt = (
                select(AgentPreset)
                .join(
                    AgentPresetVersion,
                    sa.and_(
                        AgentPresetVersion.workspace_id == AgentPreset.workspace_id,
                        AgentPresetVersion.preset_id == AgentPreset.id,
                    ),
                )
                .where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPresetVersion.id == preset_version_id,
                )
            )
            preset = (
                await self.session.execute(with_deleted(stmt))
            ).scalar_one_or_none()
        if preset is None and (preset_id is not None or slug is not None):
            preset = await self._get_preset_for_head_resolution(
                preset_id=preset_id,
                slug=slug,
            )

        if preset is None:
            return self._root_preset_failure_refs(
                preset_id=preset_id,
                slug=slug,
                code="not_found",
            )

        if preset.deleted_at is not None:
            successor_id = await self.get_successor_id_for_deleted_preset_ref(
                preset_id=preset.id,
                slug=preset.slug,
            )
            return self._root_preset_failure_refs(
                preset_id=preset.id,
                slug=preset.slug,
                code="deleted",
                successor_id=successor_id,
            )

        missing_requested_version = (
            preset_version is not None
            and await self.get_version_by_number(
                preset_id=preset.id, version=preset_version
            )
            is None
        ) or (
            preset_version_id is not None
            and await self.get_active_version(
                version_id=preset_version_id, preset_id=preset.id
            )
            is None
        )
        return self._root_preset_failure_refs(
            preset_id=preset.id,
            slug=preset.slug,
            code="not_found" if missing_requested_version else "unpublished",
        )

    @staticmethod
    def _root_preset_failure_refs(
        *,
        preset_id: uuid.UUID | None,
        slug: str | None,
        code: Literal["deleted", "unpublished", "not_found"],
        successor_id: uuid.UUID | None = None,
    ) -> ResolvedRefs:
        return ResolvedRefs(
            refs=[
                ResolvedRef(
                    resource_kind="preset",
                    slug=slug,
                    resource_id=preset_id,
                    status="skipped",
                    code=code,
                    successor_id=successor_id,
                )
            ]
        )

    def _log_skipped_agent_preset_ref(self, skipped_ref: SkippedAgentPresetRef) -> None:
        """Record a non-fatal child-head resolution skip."""

        self.logger.warning(
            "Skipping subagent preset ref during current-head resolution",
            preset_id=str(skipped_ref.preset_id) if skipped_ref.preset_id else None,
            preset_slug=skipped_ref.preset_slug,
            reason=skipped_ref.reason,
        )

    async def load_selected_mcp_integrations(
        self,
        mcp_integrations: list[str] | None,
        *,
        raise_on_missing: bool = True,
    ) -> list[MCPIntegration]:
        """Load selected MCP integrations after validating workspace access."""
        if not mcp_integrations:
            return []

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
            missing_str = sorted(str(mcp_id) for mcp_id in missing_ids)
            if raise_on_missing:
                raise TracecatValidationError(
                    f"{len(missing_ids)} MCP integrations were not found in this workspace: {missing_str}"
                )
            logger.warning(
                "Skipping missing MCP integrations while checking preset transport requirements",
                workspace_id=str(self.workspace_id),
                missing_mcp_integration_ids=missing_str,
            )

        return [
            mcp_integration
            for mcp_integration in available_mcp_integrations
            if mcp_integration.id in mcp_integration_ids
        ]

    async def validate_mcp_integrations(self, mcp_integrations: list[str]) -> None:
        """Validate MCP integration IDs for the workspace."""
        await self.load_selected_mcp_integrations(mcp_integrations)

    async def _has_stdio_mcp_integration(
        self,
        mcp_integrations: list[str] | None,
        *,
        raise_on_missing: bool = True,
    ) -> bool:
        """Return whether any selected MCP integration uses stdio transport."""
        selected_integrations = await self.load_selected_mcp_integrations(
            mcp_integrations,
            raise_on_missing=raise_on_missing,
        )
        return any(
            mcp_integration.server_type == "stdio"
            for mcp_integration in selected_integrations
        )

    async def _resolve_preset_subagent_configs(
        self,
        agents: AgentSubagentsConfig | dict[str, Any] | None,
        *,
        parent_preset_id: uuid.UUID,
        parent_slug: str,
        allow_skipped: bool = False,
    ) -> ResolvedAgentsConfig:
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
        if persisted_refs and not allow_skipped:
            await self._lock_active_subagent_presets(
                ResolvedAgentsConfig(enabled=True, subagents=persisted_refs)
            )
        resolved = await resolve_agents_config(
            self,
            agents=config,
            parent_preset_id=parent_preset_id,
            parent_slug=parent_slug,
        )
        # Skip-and-record is a runtime-resolution mechanism; authoring stays
        # strict. A ref that cannot resolve at save time is a validation error,
        # never a silent drop.
        binding = (
            resolved.to_agents_binding()
            if allow_skipped
            else self._require_available_subagents(resolved, operation="save preset")
        )
        await self._lock_active_subagent_presets(binding)
        return binding

    @staticmethod
    def _require_available_subagents(
        resolved: ResolvedAgentsConfigResult,
        *,
        operation: str,
    ) -> ResolvedAgentsConfig:
        """Reject skipped refs at authoring boundaries."""
        if resolved.skipped:
            skipped_refs = sorted(
                skipped.preset_slug or str(skipped.preset_id)
                for skipped in resolved.skipped
            )
            raise TracecatValidationError(
                f"Cannot {operation} because it references unavailable subagent "
                f"presets: {skipped_refs}",
                detail={
                    "code": "subagent_presets_unavailable",
                    "skipped": [
                        {
                            "preset_slug": skipped.preset_slug,
                            "preset_id": (
                                str(skipped.preset_id)
                                if skipped.preset_id is not None
                                else None
                            ),
                            "reason": skipped.reason,
                        }
                        for skipped in resolved.skipped
                    ],
                },
            )
        return resolved.to_agents_binding()

    async def _lock_active_subagent_presets(self, agents: ResolvedAgentsConfig) -> None:
        """Lock active child presets before saving version-owned bindings."""
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
                        stdio_env = await self.resolve_stdio_env(
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
                stdio_ref: MCPStdioServerConfig = {
                    "type": "stdio",
                    "name": mcp_integration.slug,
                    "command": mcp_integration.stdio_command,
                    "id": str(mcp_integration.id),
                }
                if mcp_integration.stdio_args:
                    stdio_ref["args"] = mcp_integration.stdio_args
                if mcp_integration.timeout is not None:
                    stdio_ref["timeout"] = mcp_integration.timeout
                stored_tools = MCPToolSummary.validate_stored(
                    mcp_integration.tools,
                    mcp_integration_id=mcp_integration.id,
                )
                if stored_tools is not None:
                    active_tools = cast(
                        list[MCPServerToolSummary],
                        [
                            tool.model_dump(exclude_none=True)
                            for tool in stored_tools
                            if tool.enabled and tool.status == "available"
                        ],
                    )
                    stdio_ref["tools"] = active_tools
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
                return await self.resolve_stdio_env(
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

    async def resolve_stdio_env(
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
            AgentPresetVersion.subagents_enabled,
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
                    agents_config=AgentSubagentsConfig(
                        enabled=(
                            subagents_enabled
                            if subagents_enabled is not None
                            else AgentSubagentsConfig.model_validate(
                                agents_json
                            ).enabled
                        )
                    ),
                    tool_approvals=tool_approvals,
                    enable_internet_access=enable_internet_access,
                ),
                subagent_eligibility=build_subagent_eligibility(
                    agents_config=AgentSubagentsConfig(
                        enabled=(
                            subagents_enabled
                            if subagents_enabled is not None
                            else AgentSubagentsConfig.model_validate(
                                agents_json
                            ).enabled
                        )
                    ),
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
                subagents_enabled,
                agents_json,
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

    async def _binding_ids_from_inputs(
        self,
        bindings: Sequence[AgentPresetSkillBindingBase],
        *,
        for_update: bool = False,
    ) -> list[uuid.UUID]:
        """Validate and normalize authored Skill ResourceHead IDs."""

        return await self._validated_skill_binding_ids(
            [binding.skill_id for binding in bindings],
            for_update=for_update,
        )

    async def _get_version_skill_binding_ids(
        self, version_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Return sorted Skill ResourceHead IDs for an immutable version."""

        stmt = select(AgentPresetVersionSkill.skill_id).where(
            AgentPresetVersionSkill.workspace_id == self.workspace_id,
            AgentPresetVersionSkill.preset_version_id == version_id,
        )
        return sorted((await self.session.execute(stmt)).scalars().all(), key=str)

    async def _validated_skill_binding_ids(
        self, skill_ids: Sequence[uuid.UUID], *, for_update: bool = False
    ) -> list[uuid.UUID]:
        """Return validated, bindable Skill ResourceHead IDs."""

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

        for skill_id in skill_ids:
            skill = skills[skill_id]
            if skill.current_version_id is None:
                raise TracecatValidationError(
                    f"Skill '{skill.name}' has no published version",
                    detail={"code": "skill_not_published", "skill_id": str(skill.id)},
                )
        version_names = list(
            (
                await self.session.scalars(
                    select(SkillVersion.name).where(
                        SkillVersion.workspace_id == self.workspace_id,
                        SkillVersion.id.in_(
                            [
                                skill.current_version_id
                                for skill in skills.values()
                                if skill.current_version_id is not None
                            ]
                        ),
                    )
                )
            ).all()
        )
        duplicate_names = sorted(
            name for name, count in Counter(version_names).items() if count > 1
        )
        if duplicate_names:
            raise TracecatValidationError(
                "Agent preset version cannot include duplicate skill names",
                detail={
                    "code": "duplicate_skill_names",
                    "skill_names": duplicate_names,
                },
            )
        return normalized_ids

    async def _compare_version_skill_bindings(
        self, base_version_id: uuid.UUID, compare_version_id: uuid.UUID
    ) -> list[AgentPresetSkillBindingChange]:
        """Return Skill ResourceHead attachment changes between versions."""

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
            if base_binding is not None and compare_binding is not None:
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
                    change_type="detached" if base_binding is not None else "attached",
                )
            )
        return skill_changes

    async def _lock_preset_row(self, preset_id: uuid.UUID) -> AgentPreset:
        """Serialize preset mutations using a row-level lock."""
        stmt = (
            select(AgentPreset)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id == preset_id,
                AgentPreset.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        preset = (await self.session.execute(stmt)).scalar_one_or_none()
        if preset is None:
            raise TracecatNotFoundError(f"Agent preset '{preset_id}' not found")
        return preset

    async def get_current_version_for_preset(
        self, preset: AgentPreset
    ) -> AgentPresetVersion:
        """Resolve a ResourceHead strictly through ``current_version_id``."""

        if preset.current_version_id is not None:
            version = await self.get_version(preset.current_version_id)
            if version is not None and version.preset_id == preset.id:
                return version
        raise TracecatNotFoundError(
            f"Agent preset '{preset.id}' has no current published version"
        )

    @require_scope("agent:update")
    @audit_log(resource_type="agent_preset", action="update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def restore_version(
        self, preset: AgentPreset, version: AgentPresetVersion
    ) -> AgentPreset:
        """Roll historical preset content forward as a new immutable version."""
        if version.preset_id != preset.id:
            raise TracecatValidationError(
                "Preset version does not belong to the selected preset"
            )

        preset = await self._lock_preset_row(preset.id)
        await self.validate_mcp_integrations(version.mcp_integrations or [])
        historical_agents = await self._get_version_agents_config(version)
        agents = await self._resolve_preset_subagent_configs(
            historical_agents,
            parent_preset_id=preset.id,
            parent_slug=preset.slug,
            allow_skipped=True,
        )
        config = AgentPresetExecutionConfigWrite.model_validate(
            {
                field: getattr(version, field)
                for field in self.EXECUTION_FIELDS
                if field != "agents"
            }
            | {"agents": historical_agents}
        )
        skill_ids = await self._validated_skill_binding_ids(
            await self._get_version_skill_binding_ids(version.id),
            for_update=True,
        )
        restored_version = await self._create_version(
            preset,
            config=config,
            skill_ids=skill_ids,
            agents=agents,
            preset_locked=True,
        )
        preset.current_version_id = restored_version.id
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

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
            "subagents_enabled",
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

        base_subagents = await self._version_subagent_diff_value(base_version)
        compare_subagents = await self._version_subagent_diff_value(compare_version)
        if base_subagents != compare_subagents:
            scalar_changes.append(
                ScalarFieldChange(
                    field="subagents",
                    old_value=base_subagents,
                    new_value=compare_subagents,
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

    @staticmethod
    def _subagent_diff_value(
        agents: AgentSubagentsConfig | ResolvedAgentsConfig,
    ) -> list[dict[str, object]]:
        """Return a stable value representation of subagent head bindings."""

        return sorted(
            [
                {
                    "preset_id": (
                        str(subagent.preset_id)
                        if isinstance(subagent, HeadAttachedSubagentRef)
                        else None
                    ),
                    "preset": subagent.preset,
                    "alias": subagent.alias,
                    "description": subagent.description,
                    "max_turns": subagent.max_turns,
                }
                for subagent in agents.subagents
            ],
            key=lambda value: str(value["alias"]),
        )

    async def _version_subagent_diff_value(
        self, version: AgentPresetVersion
    ) -> list[dict[str, object]]:
        return self._subagent_diff_value(await self._get_version_agents_config(version))

    async def _get_live_skill_successor_id(
        self,
        *,
        skill_id: uuid.UUID,
        slug: str | None,
    ) -> uuid.UUID | None:
        """Return the live owner of a deleted skill slug, without rebinding."""

        if slug is None:
            return None
        stmt = (
            select(Skill.id)
            .where(
                Skill.workspace_id == self.workspace_id,
                sa.or_(
                    Skill.slug == slug,
                    sa.and_(Skill.slug.is_(None), Skill.name == slug),
                ),
                Skill.id != skill_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            .order_by(Skill.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _build_skill_resolved_refs(
        self,
        resolved_skill_refs: ResolvedSkillRefsResult,
    ) -> ResolvedRefs | None:
        """Build value-only skill refs from the effective resolution result."""

        if not resolved_skill_refs.refs and not resolved_skill_refs.skipped:
            return None
        refs: list[ResolvedRef] = []
        for skill_ref in resolved_skill_refs.refs:
            refs.append(
                ResolvedRef(
                    resource_kind="skill",
                    slug=skill_ref.skill_name,
                    resource_id=skill_ref.skill_id,
                    resolved_version_id=skill_ref.skill_version_id,
                    manifest_sha256=skill_ref.manifest_sha256,
                    status="ok",
                )
            )
        for skipped_ref in resolved_skill_refs.skipped:
            successor_id = (
                await self._get_live_skill_successor_id(
                    skill_id=skipped_ref.skill_id,
                    slug=skipped_ref.skill_slug,
                )
                if skipped_ref.reason == "deleted"
                else None
            )
            refs.append(
                ResolvedRef(
                    resource_kind="skill",
                    slug=skipped_ref.skill_slug,
                    resource_id=skipped_ref.skill_id,
                    status="skipped",
                    code=skipped_ref.reason,
                    successor_id=successor_id,
                )
            )
        return ResolvedRefs(refs=refs)

    async def _version_to_agent_config(
        self, version: AgentPresetVersion
    ) -> AgentConfig:
        # Resolve refs only — no headers / stdio env. The resulting
        # AgentConfig is safe to cross Temporal boundaries. Trusted callers
        # (build_tool_definitions, trusted MCP server) re-resolve secrets
        # per use via resolve_mcp_integration_secrets.
        # Enrichment lookup, not a liveness check: callers decided liveness
        # before handing us a version (the resumed-session restore path
        # resolves soft-deleted child presets on purpose).
        preset = await self.get_preset(version.preset_id, include_deleted=True)
        if preset is None:
            raise TracecatNotFoundError(f"Agent preset '{version.preset_id}' not found")
        root_resolved_refs = ResolvedRefs(
            refs=[
                ResolvedRef(
                    resource_kind="preset",
                    slug=preset.slug,
                    resource_id=preset.id,
                    resolved_version_id=version.id,
                    status="ok",
                )
            ]
        )
        mcp_servers = await self.resolve_mcp_integration_refs(version.mcp_integrations)
        model_settings: dict[str, Any] = {}
        skill_resolution = await self.skills.get_resolved_skill_refs_for_preset_version(
            version.id
        )
        skill_resolved_refs = await self._build_skill_resolved_refs(skill_resolution)
        resolved_skills = skill_resolution.refs
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
        resolved_agents = await self._resolve_version_subagents(version)
        resolved_refs = merge_resolved_refs(
            root_resolved_refs,
            skill_resolved_refs,
            resolved_agents.resolved_refs,
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
            resolved_refs=resolved_refs,
        )

    async def create_version_from_current(
        self,
        preset: AgentPreset,
        *,
        current: AgentPresetVersion | None = None,
        config_updates: Mapping[str, object] | None = None,
        preset_locked: bool = False,
    ) -> AgentPresetVersion:
        """Copy the current immutable version with selected execution updates."""

        if current is None:
            current = await self.get_current_version_for_preset(preset)
        agents = await self._get_version_agents_config(current)
        config = AgentPresetExecutionConfigWrite.model_validate(
            {
                field: getattr(current, field)
                for field in self.EXECUTION_FIELDS
                if field != "agents"
            }
            | {"agents": agents}
        )
        if config_updates:
            config = AgentPresetExecutionConfigWrite.model_validate(
                config.model_dump() | dict(config_updates)
            )
        return await self._create_version(
            preset,
            config=config,
            skill_ids=await self._get_version_skill_binding_ids(current.id),
            agents=agents,
            preset_locked=preset_locked,
        )

    async def _create_version(
        self,
        preset: AgentPreset,
        *,
        config: AgentPresetExecutionConfigWrite,
        skill_ids: Sequence[uuid.UUID],
        agents: AgentSubagentsConfig | ResolvedAgentsConfig,
        preset_locked: bool = False,
    ) -> AgentPresetVersion:
        """Create one immutable version and both rollout representations."""

        if not preset_locked:
            preset = await self._lock_preset_row(preset.id)
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

        legacy_agents = await self._legacy_agents_projection(
            preset=preset,
            agents=agents,
        )
        skill_version_ids = await self._current_skill_version_ids(skill_ids)

        version = AgentPresetVersion(
            workspace_id=self.workspace_id,
            preset_id=preset.id,
            version=next_version,
            instructions=config.instructions,
            model_name=config.model_name,
            model_provider=config.model_provider,
            catalog_id=config.catalog_id,
            base_url=config.base_url,
            output_type=config.output_type,
            actions=config.actions,
            namespaces=config.namespaces,
            tool_approvals=config.tool_approvals,
            mcp_integrations=config.mcp_integrations,
            agents=legacy_agents.model_dump(mode="json"),
            subagents_enabled=agents.enabled,
            retries=config.retries,
            enable_thinking=config.enable_thinking,
            enable_internet_access=config.enable_internet_access,
        )
        self.session.add(version)
        await self.session.flush()
        for skill_id in skill_ids:
            self.session.add(
                AgentPresetVersionSkill(
                    workspace_id=self.workspace_id,
                    preset_version_id=version.id,
                    skill_id=skill_id,
                    skill_version_id=skill_version_ids[skill_id],
                )
            )
        for subagent in legacy_agents.subagents:
            self.session.add(
                AgentPresetVersionSubagent(
                    workspace_id=self.workspace_id,
                    parent_preset_version_id=version.id,
                    child_preset_id=subagent.preset_id,
                    alias=subagent.alias,
                    description=subagent.description,
                    max_turns=subagent.max_turns,
                )
            )
        await self._sync_legacy_preset_head(
            preset=preset,
            config=config,
            agents=legacy_agents,
            skill_version_ids=skill_version_ids,
        )
        await self.session.flush()
        return version

    async def _legacy_agents_projection(
        self,
        *,
        preset: AgentPreset,
        agents: AgentSubagentsConfig | ResolvedAgentsConfig,
    ) -> ResolvedAgentsConfig:
        """Return the exact legacy JSON shape understood by the old app."""

        if all(
            isinstance(subagent, ResolvedAttachedSubagentRef)
            for subagent in agents.subagents
        ):
            return ResolvedAgentsConfig.model_validate(agents.model_dump(mode="python"))
        resolved = await resolve_agents_config(
            self,
            agents=agents.model_dump(mode="python"),
            parent_preset_id=preset.id,
            parent_slug=preset.slug,
            include_runtime_config=False,
        )
        binding = self._require_available_subagents(
            resolved,
            operation="create version",
        )
        await self._lock_active_subagent_presets(binding)
        return binding

    async def _current_skill_version_ids(
        self, skill_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, uuid.UUID]:
        """Resolve ResourceHead skill edges to the legacy pinned projection."""

        if not skill_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Skill.id, Skill.current_version_id).where(
                    Skill.workspace_id == self.workspace_id,
                    Skill.id.in_(skill_ids),
                    Skill.deleted_at.is_(None),
                    Skill.archived_at.is_(None),
                )
            )
        ).tuples()
        resolved = {
            skill_id: version_id
            for skill_id, version_id in rows
            if version_id is not None
        }
        if missing := set(skill_ids) - resolved.keys():
            raise TracecatValidationError(
                "Preset skills must have a current published version",
                detail={
                    "code": "skill_not_published",
                    "skill_ids": sorted(str(skill_id) for skill_id in missing),
                },
            )
        return resolved

    async def _sync_legacy_preset_head(
        self,
        *,
        preset: AgentPreset,
        config: AgentPresetExecutionConfigWrite,
        agents: ResolvedAgentsConfig,
        skill_version_ids: Mapping[uuid.UUID, uuid.UUID],
    ) -> None:
        """Dual-write the mutable projection consumed by the old application."""

        for field in self.EXECUTION_FIELDS:
            if field == "agents":
                preset.agents = agents.model_dump(mode="json")
            else:
                setattr(preset, field, getattr(config, field))
        await self.session.execute(
            sa.delete(AgentPresetSkill).where(
                AgentPresetSkill.workspace_id == self.workspace_id,
                AgentPresetSkill.preset_id == preset.id,
            )
        )
        for skill_id, skill_version_id in skill_version_ids.items():
            self.session.add(
                AgentPresetSkill(
                    workspace_id=self.workspace_id,
                    preset_id=preset.id,
                    skill_id=skill_id,
                    skill_version_id=skill_version_id,
                )
            )
        self.session.add(preset)
