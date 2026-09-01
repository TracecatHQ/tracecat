"""Agent preset resource adapter (presets, versions, tags, skill bindings)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.catalog.types import ModelKey
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.types import SkillBindingSpec
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.authz.controls import check_scopes
from tracecat.db.models import (
    AgentFolder,
    AgentPreset,
    AgentPresetSkill,
    AgentPresetVersion,
    AgentTag,
    AgentTagLink,
    MCPIntegration,
    Skill,
    SkillVersion,
)
from tracecat.dsl.enums import PlatformAction
from tracecat.exceptions import TracecatValidationError
from tracecat.sync import (
    CatalogMappingAffectedPreset,
    CatalogMappingAffectedWorkflow,
    CatalogMappingCandidate,
    CatalogMappingRequirement,
    CatalogMappingRequirementReason,
    McpIntegrationMappingAffectedPreset,
    McpIntegrationMappingAffectedWorkflow,
    McpIntegrationMappingCandidate,
    McpIntegrationMappingRequirement,
    McpIntegrationMappingRequirementReason,
    PullDiagnostic,
)
from tracecat.workspace_sync.adapters.base import (
    DirectoryManifestAdapter,
    ImportedResource,
    NameSwapPlan,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
    SyncMappingService,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    AGENT_PRESET_ROOT,
    AgentPresetResourceSpec,
    AgentPresetSkillBinding,
    AgentPresetSubagentRef,
    McpIntegrationHint,
    WorkflowResourceSpec,
    WorkspaceSpec,
)
from tracecat.workspace_sync.types import (
    AgentPresetCatalogReference,
    AgentPresetMcpIntegrationReference,
    CatalogReference,
    CorrelatedAgentPresets,
    CorrelatedMcpIntegrationRefs,
    McpIntegrationCorrelationKey,
    McpIntegrationReference,
    WorkflowCatalogReference,
    WorkflowMcpIntegrationReference,
)
from tracecat.workspace_sync.workflow import workflow_source_path

AGENT_PRESET_FILENAME = "preset.yml"
DEFAULT_AGENT_MODEL_NAME = "gpt-5.5"


def _parse_uuid(value: object) -> uuid.UUID | None:
    """Parse a reference into a UUID; non-UUID refs are left uncorrelated."""
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


DEFAULT_AGENT_MODEL_PROVIDER = "openai"


class AgentPresetAdapter(DirectoryManifestAdapter):
    """Adapter for agent presets, their versions, tags, and skill bindings."""

    resource_type = SyncResourceType.AGENT_PRESET
    spec_attr = "agent_presets"
    model = AgentPresetResourceSpec
    read_scope = "agent:read"
    create_scope = "agent:create"
    update_scope = "agent:update"
    root = AGENT_PRESET_ROOT
    filename = AGENT_PRESET_FILENAME
    import_identity_attrs = ("slug",)
    import_identity_noun = "slug"

    async def project(
        self, workspace_service: SyncMappingService
    ) -> ResourceProjection:
        """Project agent presets into Git specs."""
        stmt = self._projection_stmt(workspace_service)
        presets = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_presets(workspace_service, presets)

    async def project_dependency_refs(
        self,
        workspace_service: SyncMappingService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project presets selected directly or referenced by slug."""
        # "Select all" short-circuits to the unfiltered projection.
        if refs.select_all:
            return await self.project(workspace_service)
        # No selectors at all means nothing to project.
        versioned_slugs = {slug for slug, _version in refs.versioned_slugs}
        slugs = set(refs.slugs) | versioned_slugs
        if not refs.local_ids and not refs.source_ids and not slugs:
            return ResourceProjection(specs={}, resources=[])

        local_ids = set(refs.local_ids)
        # Translate any source ids back to their mapped local preset ids.
        if refs.source_ids:
            local_ids.update(
                (
                    await self.local_ids_by_source_id(
                        workspace_service,
                        refs.source_ids,
                    )
                ).values()
            )
        stmt = self._projection_stmt(workspace_service)
        # Narrow the projection by whichever selectors are present: when both
        # ids and slugs are given, match either (OR); otherwise filter on the
        # single non-empty selector.
        if local_ids and slugs:
            stmt = stmt.where(
                sa.or_(
                    AgentPreset.id.in_(local_ids),
                    AgentPreset.slug.in_(slugs),
                )
            )
        elif local_ids:
            stmt = stmt.where(AgentPreset.id.in_(local_ids))
        else:
            stmt = stmt.where(AgentPreset.slug.in_(slugs))
        presets = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_presets(workspace_service, presets)

    def _projection_stmt(
        self, workspace_service: SyncMappingService
    ) -> sa.Select[tuple[AgentPreset]]:
        """Build the base eager-loaded preset projection query."""
        return (
            select(AgentPreset)
            .where(
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.deleted_at.is_(None),
            )
            .options(
                selectinload(AgentPreset.folder),
                selectinload(AgentPreset.tags),
                selectinload(AgentPreset.current_version),
            )
            .order_by(AgentPreset.slug.asc(), AgentPreset.id.asc())
        )

    async def _projection_from_presets(
        self,
        workspace_service: SyncMappingService,
        presets: list[AgentPreset],
    ) -> ResourceProjection:
        """Build one Git-owned desired head snapshot per preset."""
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for preset in presets:
            source_id = assigner.assign(preset.id, preset.slug)
            execution = preset.current_version or preset
            mcp_integrations = sorted(execution.mcp_integrations or [])
            mcp_integration_hints = await self._mcp_integration_hints(
                workspace_service,
                set(mcp_integrations),
            )
            head_spec = AgentPresetResourceSpec(
                id=source_id,
                slug=preset.slug,
                name=preset.name,
                folder_path=preset.folder.path if preset.folder else None,
                tags=sorted(tag.name for tag in preset.tags),
                instructions=execution.instructions,
                tool_approvals=execution.tool_approvals or {},
                actions=sorted(execution.actions or []),
                skills=await self._head_skill_bindings(workspace_service, preset.id),
                subagents=await self._head_subagent_refs(workspace_service, preset.id),
                catalog_id=execution.catalog_id,
                model_name=execution.model_name,
                model_provider=execution.model_provider,
                base_url=execution.base_url,
                output_type=execution.output_type,
                namespaces=sorted(execution.namespaces or []),
                mcp_integrations=mcp_integrations,
                mcp_integration_hints=mcp_integration_hints,
                retries=execution.retries,
                enable_thinking=execution.enable_thinking,
                enable_internet_access=execution.enable_internet_access,
            )
            specs[source_id] = head_spec
            resources.append(self.projected_resource(source_id, preset.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def _head_skill_bindings(
        self,
        workspace_service: SyncMappingService,
        preset_id: uuid.UUID,
    ) -> list[AgentPresetSkillBinding]:
        """Return portable Skill-head refs for a preset head."""

        stmt = (
            select(sa.func.coalesce(Skill.slug, Skill.name))
            .join(AgentPresetSkill, AgentPresetSkill.skill_id == Skill.id)
            .where(
                AgentPresetSkill.workspace_id == workspace_service.workspace_id,
                AgentPresetSkill.preset_id == preset_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            .order_by(Skill.name.asc())
        )
        return [
            AgentPresetSkillBinding(slug=slug)
            for slug in (await workspace_service.session.scalars(stmt)).all()
        ]

    async def _head_subagent_refs(
        self,
        workspace_service: SyncMappingService,
        preset_id: uuid.UUID,
    ) -> list[AgentPresetSubagentRef]:
        """Return portable child-head refs for a preset head."""

        version = await workspace_service.session.scalar(
            select(AgentPresetVersion)
            .join(AgentPreset, AgentPreset.current_version_id == AgentPresetVersion.id)
            .where(
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.id == preset_id,
            )
        )
        if version is None:
            return []
        agents = AgentSubagentsConfig.model_validate(version.agents)
        resolved_ids = {
            ref.preset_id
            for ref in agents.subagents
            if isinstance(ref, ResolvedAttachedSubagentRef)
        }
        slugs_by_id = dict(
            (
                await workspace_service.session.execute(
                    select(AgentPreset.id, AgentPreset.slug).where(
                        AgentPreset.workspace_id == workspace_service.workspace_id,
                        AgentPreset.id.in_(resolved_ids),
                        AgentPreset.deleted_at.is_(None),
                    )
                )
            )
            .tuples()
            .all()
        )
        refs = [
            AgentPresetSubagentRef(
                slug=(
                    slugs_by_id.get(ref.preset_id, ref.preset)
                    if isinstance(ref, ResolvedAttachedSubagentRef)
                    else ref.preset
                ),
                name=ref.name,
                description=ref.description,
                max_turns=ref.max_turns,
            )
            for ref in agents.subagents
        ]
        return sorted(refs, key=lambda ref: ref.name or ref.slug)

    async def _mcp_integration_hints(
        self,
        workspace_service: SyncMappingService,
        refs: set[str],
    ) -> dict[uuid.UUID, McpIntegrationHint]:
        """Return portable identity hints keyed by local MCP integration id."""
        integration_ids: set[uuid.UUID] = set()
        for ref in refs:
            try:
                integration_ids.add(uuid.UUID(ref))
            except (TypeError, ValueError):
                # Non-UUID refs predate id-based references; nothing to correlate.
                continue
        if not integration_ids:
            return {}
        check_scopes(workspace_service.role, "integration:read")
        stmt = select(
            MCPIntegration.id,
            MCPIntegration.slug,
            MCPIntegration.server_type,
            MCPIntegration.auth_type,
            MCPIntegration.name,
        ).where(
            MCPIntegration.workspace_id == workspace_service.workspace_id,
            MCPIntegration.id.in_(integration_ids),
        )
        return {
            integration_id: McpIntegrationHint(
                slug=slug,
                server_type=server_type,
                auth_type=str(auth_type),
                name=name,
            )
            for integration_id, slug, server_type, auth_type, name in (
                await workspace_service.session.execute(stmt)
            ).tuples()
        }

    async def correlate_catalog_ids(
        self,
        workspace_service: SyncMappingService,
        presets: dict[str, AgentPresetResourceSpec],
        workflows: dict[str, WorkflowResourceSpec] | None = None,
        *,
        requested_catalog_mappings: Mapping[uuid.UUID, uuid.UUID] | None = None,
    ) -> CorrelatedAgentPresets:
        """Re-map preset and workflow catalog UUIDs to enabled local rows.

        Catalog UUIDs are deployment-local. Preserve an incoming UUID when it is
        already enabled in this workspace and its model tuple matches the local
        row. Otherwise, accept a valid per-pull selection or use the only matching
        local candidate. Multiple candidates require an explicit selection rather
        than an arbitrary UUID tie-break.

        When remapping a catalog UUID, clear the snapshot's deployment-local
        ``base_url`` in both preset heads and workflow agent actions so the
        target's catalog credentials are never routed to the source deployment's
        endpoint. Unresolvable selections produce diagnostics before import writes
        begin instead of reaching a foreign-key violation.
        """
        requested_catalog_mappings = requested_catalog_mappings or {}
        workflows = workflows or {}
        catalog_service = AgentCatalogService(session=workspace_service.session)
        references_by_catalog_id: dict[uuid.UUID, list[CatalogReference]] = {}
        present_catalog_ids: set[uuid.UUID] = set()
        diagnostics: list[PullDiagnostic] = []
        invalid_catalog_ids: set[uuid.UUID] = set()
        missing_tuple_diagnostics: list[tuple[uuid.UUID, PullDiagnostic]] = []

        for source_id, preset in sorted(presets.items()):
            catalog_id = preset.catalog_id
            if catalog_id is None:
                continue
            present_catalog_ids.add(catalog_id)
            if not preset.model_provider or not preset.model_name:
                # The tuple is only needed to correlate a non-local UUID.
                # Defer the diagnostic until local enablement is known.
                missing_tuple_diagnostics.append(
                    (
                        catalog_id,
                        PullDiagnostic(
                            workflow_path=self.source_path(source_id),
                            workflow_title=preset.name,
                            error_type="validation",
                            message=(
                                f"Agent preset {preset.slug!r} references a "
                                "non-local model catalog entry but does not "
                                "include model_provider and model_name for "
                                "correlation."
                            ),
                            details={
                                "preset_slug": preset.slug,
                                "catalog_id": str(catalog_id),
                            },
                        ),
                    )
                )
                continue
            references_by_catalog_id.setdefault(catalog_id, []).append(
                AgentPresetCatalogReference(
                    path=self.source_path(source_id),
                    preset_slug=preset.slug,
                    preset_name=preset.name,
                    version_number=1,
                    model_key=ModelKey(
                        preset.model_provider,
                        preset.model_name,
                    ),
                )
            )

        workflow_action_types = (PlatformAction.AI_AGENT, PlatformAction.AI_ACTION)
        for source_id, workflow in sorted(workflows.items()):
            for action in workflow.definition.actions:
                if action.action not in workflow_action_types:
                    continue
                nested_model = action.args.get("model")
                nested_model = nested_model if isinstance(nested_model, dict) else None
                merged_args = (
                    {**action.args, **nested_model}
                    if nested_model is not None
                    else action.args
                )
                raw_catalog_id = merged_args.get("catalog_id")
                model_provider = merged_args.get("model_provider")
                model_name = merged_args.get("model_name")
                if raw_catalog_id is None or not model_provider or not model_name:
                    continue
                try:
                    catalog_id = uuid.UUID(str(raw_catalog_id))
                except (TypeError, ValueError):
                    # Not a literal UUID (e.g. a template expression evaluated
                    # at runtime): leave the action untouched.
                    continue
                present_catalog_ids.add(catalog_id)
                references_by_catalog_id.setdefault(catalog_id, []).append(
                    WorkflowCatalogReference(
                        path=workflow_source_path(source_id),
                        workflow_source_id=source_id,
                        workflow_title=workflow.definition.title,
                        action_ref=action.ref,
                        model_key=ModelKey(
                            str(model_provider),
                            str(model_name),
                        ),
                    )
                )

        enabled_catalog_models = await catalog_service.enabled_catalog_models(
            org_id=workspace_service.organization_id,
            workspace_id=workspace_service.workspace_id,
            catalog_ids=present_catalog_ids,
        )

        for catalog_id, diagnostic in missing_tuple_diagnostics:
            if catalog_id in enabled_catalog_models:
                continue
            diagnostics.append(diagnostic)
            invalid_catalog_ids.add(catalog_id)

        sorted_references_by_catalog_id = sorted(references_by_catalog_id.items())
        for catalog_id, references in sorted_references_by_catalog_id:
            model_keys = {reference.model_key for reference in references}
            if len(model_keys) <= 1:
                continue
            first = references[0]
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=first.path,
                    workflow_title=self._catalog_reference_title(first),
                    error_type="validation",
                    message=(
                        f"Source catalog entry {catalog_id} is used with conflicting "
                        "model_provider and model_name values. A source catalog UUID "
                        "must identify exactly one model."
                    ),
                    details={
                        "code": "catalog_model_identity_conflict",
                        "catalog_id": str(catalog_id),
                        "models": [
                            {
                                "model_provider": model_key.model_provider,
                                "model_name": model_key.model_name,
                            }
                            for model_key in sorted(model_keys)
                        ],
                    },
                )
            )
            invalid_catalog_ids.add(catalog_id)

        for catalog_id, references in sorted_references_by_catalog_id:
            if catalog_id in invalid_catalog_ids:
                continue
            local_model_key = enabled_catalog_models.get(catalog_id)
            if local_model_key is None:
                continue
            manifest_model_key = references[0].model_key
            if manifest_model_key == local_model_key:
                continue
            first = references[0]
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=first.path,
                    workflow_title=self._catalog_reference_title(first),
                    error_type="validation",
                    message=(
                        f"Enabled local catalog entry {catalog_id} identifies "
                        f"{local_model_key.model_provider!r} / "
                        f"{local_model_key.model_name!r}, but the repository "
                        f"manifest identifies {manifest_model_key.model_provider!r} / "
                        f"{manifest_model_key.model_name!r}."
                    ),
                    details={
                        "code": "catalog_model_identity_mismatch",
                        "catalog_id": str(catalog_id),
                        "manifest_model": {
                            "model_provider": manifest_model_key.model_provider,
                            "model_name": manifest_model_key.model_name,
                        },
                        "local_model": {
                            "model_provider": local_model_key.model_provider,
                            "model_name": local_model_key.model_name,
                        },
                    },
                )
            )
            invalid_catalog_ids.add(catalog_id)

        models = {
            references[0].model_key
            for catalog_id, references in references_by_catalog_id.items()
            if catalog_id not in invalid_catalog_ids
            and catalog_id not in enabled_catalog_models
        }
        candidates_by_model = await catalog_service.catalog_candidates_by_models(
            org_id=workspace_service.organization_id,
            workspace_id=workspace_service.workspace_id,
            models=models,
        )
        resolved_catalog_ids: dict[uuid.UUID, uuid.UUID] = {}
        requirements: list[CatalogMappingRequirement] = []

        for catalog_id, references in sorted_references_by_catalog_id:
            if catalog_id in invalid_catalog_ids:
                continue
            if catalog_id in enabled_catalog_models:
                continue
            model_key = references[0].model_key
            candidates = candidates_by_model.get(model_key, [])
            requested_target = requested_catalog_mappings.get(catalog_id)

            if requested_target is not None:
                candidate_ids = {candidate.catalog_id for candidate in candidates}
                if requested_target in candidate_ids:
                    resolved_catalog_ids[catalog_id] = requested_target
                    continue
                self._append_catalog_mapping_requirement(
                    requirements=requirements,
                    diagnostics=diagnostics,
                    catalog_id=catalog_id,
                    model_key=model_key,
                    references=references,
                    candidates=candidates,
                    reason="invalid_selection",
                )
                continue

            if len(candidates) == 1:
                resolved_catalog_ids[catalog_id] = candidates[0].catalog_id
                continue
            if len(candidates) > 1:
                self._append_catalog_mapping_requirement(
                    requirements=requirements,
                    diagnostics=diagnostics,
                    catalog_id=catalog_id,
                    model_key=model_key,
                    references=references,
                    candidates=candidates,
                    reason="ambiguous",
                )
                continue

            self._append_unavailable_catalog_diagnostics(
                diagnostics=diagnostics,
                catalog_id=catalog_id,
                references=references,
            )

        unused_requested_catalog_ids = (
            set(requested_catalog_mappings) - present_catalog_ids
        )
        for catalog_id in sorted(unused_requested_catalog_ids):
            diagnostics.append(
                PullDiagnostic(
                    workflow_path="",
                    workflow_title=None,
                    error_type="validation",
                    message=(
                        f"Catalog mapping selection for source {catalog_id} does not "
                        "appear in this repository snapshot."
                    ),
                    details={
                        "catalog_id": str(catalog_id),
                        "code": "catalog_mapping_source_not_found",
                    },
                )
            )

        if not resolved_catalog_ids:
            return CorrelatedAgentPresets(
                presets=presets,
                workflows=workflows,
                diagnostics=diagnostics,
                requirements=requirements,
            )

        correlated_presets: dict[str, AgentPresetResourceSpec] = {}
        for source_id, preset in sorted(presets.items()):
            local_catalog_id = (
                resolved_catalog_ids.get(preset.catalog_id)
                if preset.catalog_id is not None
                else None
            )
            correlated_presets[source_id] = (
                preset
                if local_catalog_id is None
                else preset.model_copy(
                    update={"catalog_id": local_catalog_id, "base_url": None}
                )
            )

        correlated_workflows: dict[str, WorkflowResourceSpec] = {}
        for source_id, workflow in sorted(workflows.items()):
            correlated_action_specs = list(workflow.definition.actions)
            workflow_rewritten = False
            for index, action in enumerate(workflow.definition.actions):
                if action.action not in (
                    PlatformAction.AI_AGENT,
                    PlatformAction.AI_ACTION,
                ):
                    continue
                nested_model = action.args.get("model")
                nested_model = nested_model if isinstance(nested_model, dict) else None
                merged_args = (
                    {**action.args, **nested_model}
                    if nested_model is not None
                    else action.args
                )
                raw_catalog_id = merged_args.get("catalog_id")
                if (
                    not raw_catalog_id
                    or not merged_args.get("model_provider")
                    or not merged_args.get("model_name")
                ):
                    continue
                try:
                    catalog_id = uuid.UUID(str(raw_catalog_id))
                except (TypeError, ValueError):
                    continue
                local_catalog_id = resolved_catalog_ids.get(catalog_id)
                if local_catalog_id is None:
                    continue

                new_catalog_id = str(local_catalog_id)
                new_args = dict(action.args)
                if "catalog_id" in new_args:
                    new_args["catalog_id"] = new_catalog_id
                if "base_url" in new_args:
                    new_args["base_url"] = None
                if nested_model is not None:
                    new_model = dict(nested_model)
                    if "catalog_id" in new_model:
                        new_model["catalog_id"] = new_catalog_id
                    if "base_url" in new_model:
                        new_model["base_url"] = None
                    new_args["model"] = new_model
                correlated_action_specs[index] = action.model_copy(
                    update={"args": new_args}
                )
                workflow_rewritten = True

            correlated_workflows[source_id] = (
                workflow
                if not workflow_rewritten
                else workflow.model_copy(
                    update={
                        "definition": workflow.definition.model_copy(
                            update={"actions": correlated_action_specs}
                        )
                    }
                )
            )

        return CorrelatedAgentPresets(
            presets=correlated_presets,
            workflows=correlated_workflows,
            diagnostics=diagnostics,
            requirements=requirements,
        )

    def _append_catalog_mapping_requirement(
        self,
        *,
        requirements: list[CatalogMappingRequirement],
        diagnostics: list[PullDiagnostic],
        catalog_id: uuid.UUID,
        model_key: ModelKey,
        references: list[CatalogReference],
        candidates: list[CatalogMappingCandidate],
        reason: CatalogMappingRequirementReason,
    ) -> None:
        """Append one grouped mapping requirement and its blocking diagnostic."""
        if not candidates:
            self._append_unavailable_catalog_diagnostics(
                diagnostics=diagnostics,
                catalog_id=catalog_id,
                references=references,
            )
            return

        if reason == "ambiguous":
            message = (
                f"Model {model_key.model_provider!r} / {model_key.model_name!r} "
                f"matches {len(candidates)} enabled target catalogs. Choose the "
                "target model before applying this pull."
            )
        else:
            message = (
                f"The selected target is not an enabled match for model "
                f"{model_key.model_provider!r} / {model_key.model_name!r}. "
                "Choose an available target before applying this pull."
            )
        first = references[0]
        diagnostics.append(
            PullDiagnostic(
                workflow_path=first.path,
                workflow_title=self._catalog_reference_title(first),
                error_type="dependency",
                message=message,
                details={
                    "code": "catalog_mapping_required",
                    "catalog_id": str(catalog_id),
                    "model_provider": model_key.model_provider,
                    "model_name": model_key.model_name,
                    "reason": reason,
                },
            )
        )
        requirements.append(
            CatalogMappingRequirement(
                source_catalog_id=catalog_id,
                model_provider=model_key.model_provider,
                model_name=model_key.model_name,
                reason=reason,
                message=message,
                candidates=list(candidates),
                affected_presets=[
                    CatalogMappingAffectedPreset(
                        preset_slug=reference.preset_slug,
                        preset_name=reference.preset_name,
                        version=reference.version_number,
                        path=reference.path,
                    )
                    for reference in references
                    if isinstance(reference, AgentPresetCatalogReference)
                ],
                affected_workflows=[
                    CatalogMappingAffectedWorkflow(
                        workflow_source_id=reference.workflow_source_id,
                        workflow_path=reference.path,
                        workflow_title=reference.workflow_title,
                        action_ref=reference.action_ref,
                    )
                    for reference in references
                    if isinstance(reference, WorkflowCatalogReference)
                ],
            )
        )

    def _append_unavailable_catalog_diagnostics(
        self,
        *,
        diagnostics: list[PullDiagnostic],
        catalog_id: uuid.UUID,
        references: list[CatalogReference],
    ) -> None:
        """Append per-reference diagnostics when no candidate is available."""
        for reference in references:
            if isinstance(reference, AgentPresetCatalogReference):
                message = (
                    f"Agent preset {reference.preset_slug!r} version "
                    f"{reference.version_number} requires model "
                    f"{reference.model_key.model_provider!r} / "
                    f"{reference.model_key.model_name!r}, but no matching "
                    "enabled model is configured for this workspace."
                )
                details = {
                    "preset_slug": reference.preset_slug,
                    "preset_version": reference.version_number,
                    "catalog_id": str(catalog_id),
                    "model_provider": reference.model_key.model_provider,
                    "model_name": reference.model_key.model_name,
                }
            else:
                message = (
                    f"Workflow {reference.workflow_title!r} action "
                    f"{reference.action_ref!r} requires model "
                    f"{reference.model_key.model_provider!r} / "
                    f"{reference.model_key.model_name!r}, but no matching "
                    "enabled model is configured for this workspace."
                )
                details = {
                    "workflow_source_id": reference.workflow_source_id,
                    "action_ref": reference.action_ref,
                    "catalog_id": str(catalog_id),
                    "model_provider": reference.model_key.model_provider,
                    "model_name": reference.model_key.model_name,
                }
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=reference.path,
                    workflow_title=self._catalog_reference_title(reference),
                    error_type="dependency",
                    message=message,
                    details=details,
                )
            )

    def _catalog_reference_title(self, reference: CatalogReference) -> str:
        """Return the owning preset or workflow title for a catalog reference."""
        if isinstance(reference, AgentPresetCatalogReference):
            return reference.preset_name
        return reference.workflow_title

    async def correlate_mcp_integration_refs(
        self,
        workspace_service: SyncMappingService,
        presets: dict[str, AgentPresetResourceSpec],
        workflows: dict[str, WorkflowResourceSpec] | None = None,
        *,
        requested_mcp_integration_mappings: Mapping[uuid.UUID, uuid.UUID] | None = None,
    ) -> CorrelatedMcpIntegrationRefs:
        """Re-map preset and workflow MCP integration UUIDs to local rows.

        MCP integration UUIDs are workspace-local, and the integrations themselves
        never sync. An exact slug/server_type/auth_type match against an exported
        identity hint auto-resolves. Anything else requires an explicit selection
        for the current pull.
        """
        requested_mappings = requested_mcp_integration_mappings or {}
        workflows = workflows or {}
        diagnostics: list[PullDiagnostic] = []
        references: dict[uuid.UUID, list[McpIntegrationReference]] = {}
        meta_by_source_id: dict[uuid.UUID, McpIntegrationHint] = {}
        conflicting_meta_ids: set[uuid.UUID] = set()

        for source_id, preset in sorted(presets.items()):
            for ref in preset.mcp_integrations:
                integration_id = _parse_uuid(ref)
                if integration_id is None:
                    continue
                meta = preset.mcp_integration_hints.get(integration_id)
                if meta is not None:
                    meta_key = McpIntegrationCorrelationKey(
                        slug=meta.slug,
                        server_type=meta.server_type,
                        auth_type=meta.auth_type,
                    )
                    known = meta_by_source_id.get(integration_id)
                    if known is None:
                        meta_by_source_id[integration_id] = meta
                    elif (
                        McpIntegrationCorrelationKey(
                            slug=known.slug,
                            server_type=known.server_type,
                            auth_type=known.auth_type,
                        )
                        != meta_key
                    ):
                        # One source UUID must identify one integration.
                        conflicting_meta_ids.add(integration_id)
                references.setdefault(integration_id, []).append(
                    AgentPresetMcpIntegrationReference(
                        path=self.source_path(source_id),
                        preset_slug=preset.slug,
                        preset_name=preset.name,
                        version_number=1,
                        meta=meta,
                    )
                )

        workflow_action_types = (PlatformAction.AI_AGENT, PlatformAction.AI_ACTION)
        for source_id, workflow in sorted(workflows.items()):
            for action in workflow.definition.actions:
                if action.action not in workflow_action_types:
                    continue
                raw_refs = action.args.get("mcp_integrations")
                if not isinstance(raw_refs, list):
                    continue
                for ref in raw_refs:
                    integration_id = _parse_uuid(ref)
                    if integration_id is None:
                        continue
                    references.setdefault(integration_id, []).append(
                        WorkflowMcpIntegrationReference(
                            path=workflow_source_path(source_id),
                            workflow_source_id=source_id,
                            workflow_title=workflow.definition.title,
                            action_ref=action.ref,
                        )
                    )

        present_source_ids = set(references)
        unused_requested = set(requested_mappings) - present_source_ids
        for source_integration_id in sorted(unused_requested):
            diagnostics.append(
                PullDiagnostic(
                    workflow_path="",
                    workflow_title=None,
                    error_type="validation",
                    message=(
                        f"MCP integration mapping selection for source "
                        f"{source_integration_id} does not appear in this "
                        "repository snapshot."
                    ),
                    details={
                        "code": "mcp_integration_mapping_source_not_found",
                        "mcp_integration_id": str(source_integration_id),
                    },
                )
            )

        if not references:
            return CorrelatedMcpIntegrationRefs(
                presets=presets,
                workflows=workflows,
                diagnostics=diagnostics,
                requirements=[],
            )

        local_integrations = await self._local_mcp_integrations(workspace_service)
        local_by_id = {row.mcp_integration_id: row for row in local_integrations}
        by_integration_key: dict[
            McpIntegrationCorrelationKey, list[McpIntegrationMappingCandidate]
        ] = {}
        for row in local_integrations:
            key = McpIntegrationCorrelationKey(
                slug=row.slug,
                server_type=row.server_type,
                auth_type=str(row.auth_type),
            )
            by_integration_key.setdefault(key, []).append(row)

        resolved: dict[uuid.UUID, uuid.UUID] = {}
        requirements: list[McpIntegrationMappingRequirement] = []
        local_ids = {row.mcp_integration_id for row in local_integrations}

        for source_integration_id, refs in sorted(references.items()):
            requested_target = requested_mappings.get(source_integration_id)
            meta = meta_by_source_id.get(source_integration_id)
            if requested_target is not None:
                if requested_target in local_ids:
                    resolved[source_integration_id] = requested_target
                    continue
                self._append_mcp_integration_requirement(
                    requirements=requirements,
                    diagnostics=diagnostics,
                    source_integration_id=source_integration_id,
                    meta=meta,
                    references=refs,
                    local_integrations=local_integrations,
                    reason="invalid_selection",
                )
                continue

            if source_integration_id in conflicting_meta_ids:
                # Neither copy of the hint can be trusted; require a choice.
                self._append_mcp_integration_requirement(
                    requirements=requirements,
                    diagnostics=diagnostics,
                    source_integration_id=source_integration_id,
                    meta=None,
                    references=refs,
                    local_integrations=local_integrations,
                    reason="conflicting_metadata",
                )
                continue

            local_integration = local_by_id.get(source_integration_id)
            if local_integration is not None and (
                meta is None
                or McpIntegrationCorrelationKey(
                    slug=local_integration.slug,
                    server_type=local_integration.server_type,
                    auth_type=local_integration.auth_type,
                )
                == McpIntegrationCorrelationKey(
                    slug=meta.slug,
                    server_type=meta.server_type,
                    auth_type=meta.auth_type,
                )
            ):
                resolved[source_integration_id] = source_integration_id
                continue

            if meta is not None:
                matches = by_integration_key.get(
                    McpIntegrationCorrelationKey(
                        slug=meta.slug,
                        server_type=meta.server_type,
                        auth_type=meta.auth_type,
                    ),
                    [],
                )
                if len(matches) == 1:
                    # Deterministic correlation-key hit; intentionally not persisted.
                    resolved[source_integration_id] = matches[0].mcp_integration_id
                    continue

            self._append_mcp_integration_requirement(
                requirements=requirements,
                diagnostics=diagnostics,
                source_integration_id=source_integration_id,
                meta=meta,
                references=refs,
                local_integrations=local_integrations,
                reason="unresolved",
            )

        if not resolved:
            return CorrelatedMcpIntegrationRefs(
                presets=presets,
                workflows=workflows,
                diagnostics=diagnostics,
                requirements=requirements,
            )

        local_hint_by_id = {
            row.mcp_integration_id: McpIntegrationHint(
                slug=row.slug,
                server_type=row.server_type,
                auth_type=str(row.auth_type),
                name=row.name,
            )
            for row in local_integrations
        }
        correlated_presets: dict[str, AgentPresetResourceSpec] = {}
        for source_id, preset in sorted(presets.items()):
            # Resolved ids and hints take the local shape so repeat previews
            # converge on the local projection.
            rewritten: list[str] = []
            rewritten_hints: dict[uuid.UUID, McpIntegrationHint] = {}
            for ref in preset.mcp_integrations:
                ref_id = _parse_uuid(ref)
                if ref_id is not None and ref_id in resolved:
                    local_id = resolved[ref_id]
                    rewritten.append(str(local_id))
                    rewritten_hints[local_id] = local_hint_by_id[local_id]
                else:
                    rewritten.append(ref)
                    if ref_id is not None and (
                        hint := preset.mcp_integration_hints.get(ref_id)
                    ):
                        rewritten_hints[ref_id] = hint
            correlated_presets[source_id] = (
                preset
                if rewritten == preset.mcp_integrations
                and rewritten_hints == preset.mcp_integration_hints
                else preset.model_copy(
                    update={
                        "mcp_integrations": rewritten,
                        "mcp_integration_hints": rewritten_hints,
                    }
                )
            )

        correlated_workflows: dict[str, WorkflowResourceSpec] = {}
        for source_id, workflow in sorted(workflows.items()):
            action_specs = list(workflow.definition.actions)
            rewritten_workflow = False
            for index, action in enumerate(workflow.definition.actions):
                if action.action not in workflow_action_types:
                    continue
                raw_refs = action.args.get("mcp_integrations")
                if not isinstance(raw_refs, list):
                    continue
                rewritten_refs = [
                    str(resolved[parsed])
                    if (parsed := _parse_uuid(ref)) is not None and parsed in resolved
                    else ref
                    for ref in raw_refs
                ]
                if rewritten_refs == raw_refs:
                    continue
                new_args = dict(action.args)
                new_args["mcp_integrations"] = rewritten_refs
                action_specs[index] = action.model_copy(update={"args": new_args})
                rewritten_workflow = True

            correlated_workflows[source_id] = (
                workflow
                if not rewritten_workflow
                else workflow.model_copy(
                    update={
                        "definition": workflow.definition.model_copy(
                            update={"actions": action_specs}
                        )
                    }
                )
            )

        return CorrelatedMcpIntegrationRefs(
            presets=correlated_presets,
            workflows=correlated_workflows,
            diagnostics=diagnostics,
            requirements=requirements,
        )

    async def _local_mcp_integrations(
        self,
        workspace_service: SyncMappingService,
    ) -> list[McpIntegrationMappingCandidate]:
        """Return the workspace's MCP integrations ordered by slug.

        Loads only the correlation metadata columns; encrypted credential columns
        and the OAuth relationship must never enter this path.
        """
        check_scopes(workspace_service.role, "integration:read")
        stmt = (
            select(
                MCPIntegration.id,
                MCPIntegration.slug,
                MCPIntegration.name,
                MCPIntegration.server_type,
                MCPIntegration.auth_type,
            )
            .where(MCPIntegration.workspace_id == workspace_service.workspace_id)
            .order_by(MCPIntegration.slug.asc())
        )
        return [
            McpIntegrationMappingCandidate(
                mcp_integration_id=integration_id,
                slug=slug,
                name=name,
                server_type=server_type,
                auth_type=str(auth_type),
            )
            for integration_id, slug, name, server_type, auth_type in (
                await workspace_service.session.execute(stmt)
            ).tuples()
        ]

    def _append_mcp_integration_requirement(
        self,
        *,
        requirements: list[McpIntegrationMappingRequirement],
        diagnostics: list[PullDiagnostic],
        source_integration_id: uuid.UUID,
        meta: McpIntegrationHint | None,
        references: list[McpIntegrationReference],
        local_integrations: list[McpIntegrationMappingCandidate],
        reason: McpIntegrationMappingRequirementReason,
    ) -> None:
        """Append one grouped MCP mapping requirement and its blocking diagnostic."""
        first = references[0]
        label = f"{meta.slug!r}" if meta is not None else str(source_integration_id)
        if not local_integrations:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=first.path,
                    workflow_title=self._mcp_reference_title(first),
                    error_type="dependency",
                    message=(
                        f"MCP integration {label} is referenced by this snapshot, "
                        "but no MCP integrations are configured for this workspace."
                    ),
                    details={
                        "code": "mcp_integration_mapping_required",
                        "mcp_integration_id": str(source_integration_id),
                        "reason": reason,
                    },
                )
            )
            return

        # Slug-matched candidate first so the likely target leads the picker.
        candidates = sorted(
            local_integrations,
            key=lambda row: (
                meta is None or row.slug != meta.slug,
                row.slug,
            ),
        )
        if reason == "invalid_selection":
            message = (
                f"The selected target is not an MCP integration in this workspace. "
                f"Choose an available MCP integration for source {label}."
            )
        elif reason == "conflicting_metadata":
            message = (
                f"MCP integration {label} carries conflicting correlation "
                "metadata across this snapshot's files. Choose the target "
                "integration explicitly."
            )
        else:
            message = (
                f"MCP integration {label} could not be matched to a local MCP "
                "integration. Choose the target integration before applying "
                "this pull."
            )
        diagnostics.append(
            PullDiagnostic(
                workflow_path=first.path,
                workflow_title=self._mcp_reference_title(first),
                error_type="dependency",
                message=message,
                details={
                    "code": "mcp_integration_mapping_required",
                    "mcp_integration_id": str(source_integration_id),
                    "reason": reason,
                },
            )
        )
        requirements.append(
            McpIntegrationMappingRequirement(
                source_mcp_integration_id=source_integration_id,
                slug=meta.slug if meta else None,
                name=meta.name if meta else None,
                server_type=meta.server_type if meta else None,
                auth_type=meta.auth_type if meta else None,
                reason=reason,
                message=message,
                candidates=candidates,
                affected_presets=[
                    McpIntegrationMappingAffectedPreset(
                        preset_slug=reference.preset_slug,
                        preset_name=reference.preset_name,
                        version=reference.version_number,
                        path=reference.path,
                    )
                    for reference in references
                    if isinstance(reference, AgentPresetMcpIntegrationReference)
                ],
                affected_workflows=[
                    McpIntegrationMappingAffectedWorkflow(
                        workflow_source_id=reference.workflow_source_id,
                        workflow_path=reference.path,
                        workflow_title=reference.workflow_title,
                        action_ref=reference.action_ref,
                    )
                    for reference in references
                    if isinstance(reference, WorkflowMcpIntegrationReference)
                ],
            )
        )

    def _mcp_reference_title(self, reference: McpIntegrationReference) -> str:
        """Return the owning preset or workflow title for an MCP reference."""
        if isinstance(reference, AgentPresetMcpIntegrationReference):
            return reference.preset_name
        return reference.workflow_title

    async def import_specs(
        self,
        workspace_service: SyncMappingService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile agent preset specs into the local database.

        Imports presets in two passes: the first upserts each preset, folder,
        and tag set; the second resolves subagent references and skill bindings
        in topological order and pins each preset's current version.
        """
        presets = workspace_spec.agent_presets
        # Compute the parent-after-subagent order up front so pass 2 can resolve
        # subagent refs once their child presets already exist.
        import_order = self._preset_import_order(presets)
        swap = await self.plan_name_swap(
            workspace_service,
            targets={source_id: spec.slug for source_id, spec in presets.items()},
            model=AgentPreset,
            name_column=AgentPreset.slug,
            noun="slug",
            kind_label="Agent preset",
            owner_label="preset",
            error_cls=TracecatValidationError,
            options=(selectinload(AgentPreset.tags),),
            row_predicates=(AgentPreset.deleted_at.is_(None),),
            availability_predicates=(AgentPreset.deleted_at.is_(None),),
        )
        imported: list[ImportedResource] = []
        preset_by_source_id: dict[str, AgentPreset] = {}
        preset_service = AgentPresetService(
            workspace_service.session,
            role=workspace_service.role,
        )
        # Pass 1: upsert every preset's metadata, folder, and tags. Slug order
        # keeps creation deterministic; head config and versions wait for pass 2.
        for source_id, spec in sorted(presets.items()):
            preset = await self._preset_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                swap=swap,
            )
            # Create a new preset with sensible model defaults when none exists;
            # otherwise just realign the existing row's slug to the spec.
            if preset is None:
                preset = AgentPreset(
                    workspace_id=workspace_service.workspace_id,
                    slug=spec.slug,
                    name=spec.name,
                    model_name=DEFAULT_AGENT_MODEL_NAME,
                    model_provider=DEFAULT_AGENT_MODEL_PROVIDER,
                    agents={"subagents": []},
                )
            else:
                preset.slug = spec.slug
            self._apply_preset_metadata(preset, spec)
            folder = await self._ensure_agent_folder(
                workspace_service, spec.folder_path
            )
            preset.folder_id = folder.id if folder is not None else None
            workspace_service.session.add(preset)
            # Flush so the preset has an id before tag links reference it.
            await workspace_service.session.flush()
            await self._replace_agent_tags(workspace_service, preset, spec.tags)
            preset_by_source_id[source_id] = preset

        # Pass 2: walk presets in topological order so every subagent child has
        # already been upserted by the time its parent resolves references.
        for source_id in import_order:
            spec = presets[source_id]
            preset = preset_by_source_id[source_id]

            # Apply desired config and topology after every child head exists.
            self._apply_preset_head_spec(preset, spec)
            resolved_subagents = await self._resolved_subagents_config(
                workspace_service, spec
            )
            preset.agents = resolved_subagents.model_dump(mode="json")
            workspace_service.session.add(preset)
            await workspace_service.session.flush()
            skill_targets = await self._skill_binding_targets_for_spec(
                workspace_service,
                spec,
            )
            binding_specs = [
                SkillBindingSpec(skill.id, skill_version.id)
                for skill, skill_version in skill_targets
            ]

            await preset_service.reconcile_and_publish_head(
                preset,
                binding_specs=binding_specs,
            )
            imported.append(self.imported_resource(source_id, preset.id))
        return imported

    def _preset_import_order(
        self,
        presets: Mapping[str, AgentPresetResourceSpec],
    ) -> list[str]:
        """Return source ids ordered so each preset follows its subagents.

        Topologically sorts presets by their subagent references so that a
        parent is imported only after every subagent it depends on. Raises
        :class:`TracecatValidationError` on duplicate slugs or cyclic
        references.
        """
        # Index slug -> source id so subagent refs (which name slugs) can be
        # mapped back to specs; duplicate slugs make the graph ambiguous.
        slug_to_source_id: dict[str, str] = {}
        for source_id, spec in sorted(presets.items()):
            if spec.slug in slug_to_source_id:
                raise TracecatValidationError(
                    f"Agent preset sync specs must have unique slugs: {spec.slug!r}"
                )
            slug_to_source_id[spec.slug] = source_id

        # `visiting` is the current DFS stack (re-entering it means a cycle);
        # `visited` is fully-processed; `ordered` collects the final sequence.
        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[str] = []

        def visit(source_id: str) -> None:
            """Depth-first visit a preset and append it after its subagents."""
            # Already emitted: nothing more to do.
            if source_id in visited:
                return
            # Re-entering a node still on the stack closes a cycle.
            if source_id in visiting:
                raise TracecatValidationError(
                    "Cyclic agent preset subagent reference detected during import"
                )
            # Mark on-stack, recurse into each subagent child first, then mark
            # done and append so children always precede this parent.
            visiting.add(source_id)
            spec = presets[source_id]
            for subagent in sorted(spec.subagents, key=lambda item: item.slug):
                # Ignore refs to slugs not present in this sync batch.
                if child_source_id := slug_to_source_id.get(subagent.slug):
                    visit(child_source_id)
            visiting.remove(source_id)
            visited.add(source_id)
            ordered.append(source_id)

        for source_id in sorted(presets):
            visit(source_id)
        return ordered

    def _apply_preset_metadata(
        self,
        preset: AgentPreset,
        spec: AgentPresetResourceSpec,
    ) -> None:
        """Copy non-versioned metadata fields onto ``preset``."""
        preset.name = spec.name

    def _apply_preset_head_spec(
        self,
        preset: AgentPreset,
        spec: AgentPresetResourceSpec,
    ) -> None:
        """Copy publishable fields from ``spec`` onto the live preset head."""
        for key, value in self._version_attrs_from_spec(spec).items():
            setattr(preset, key, value)

    async def _preset_for_import(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
        spec: AgentPresetResourceSpec,
        swap: NameSwapPlan[AgentPreset],
    ) -> AgentPreset | None:
        """Resolve the existing preset to update for ``source_id``, if any.

        Prefers the preset already mapped to ``source_id`` (validating the slug
        is still free), then falls back to matching on slug. Returns ``None``
        when no preset exists and a new one must be created.
        """
        # Prefer the preset already mapped to this source id; the mapping is the
        # authoritative link even if the spec slug has since changed.
        preset = swap.mapped_by_source_id.get(source_id) or (
            await self._preset_by_source_id(
                workspace_service,
                source_id=source_id,
            )
        )
        if preset is not None:
            # Guard the (possibly renamed) slug isn't claimed by another preset.
            await swap.ensure_available(
                workspace_service,
                source_id=source_id,
                name=spec.slug,
                row_id=preset.id,
            )
            return preset

        # No mapping yet: fall back to adopting an existing preset by slug.
        return await workspace_service.session.scalar(
            select(AgentPreset)
            .where(
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.slug == spec.slug,
                AgentPreset.deleted_at.is_(None),
            )
            .options(selectinload(AgentPreset.tags))
        )

    async def _preset_by_source_id(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
    ) -> AgentPreset | None:
        """Load the preset mapped to ``source_id`` via the sync mapping, if any."""
        return await self._row_by_source_id(
            workspace_service,
            source_id=source_id,
            model=AgentPreset,
            options=(selectinload(AgentPreset.tags),),
            row_predicates=(AgentPreset.deleted_at.is_(None),),
        )

    async def _ensure_agent_folder(
        self,
        workspace_service: SyncMappingService,
        folder_path: str | None,
    ) -> AgentFolder | None:
        """Resolve ``folder_path`` to an :class:`AgentFolder`, creating segments.

        Walks each ``/``-separated segment, creating any missing intermediate
        folders, and returns the leaf folder. Returns ``None`` when
        ``folder_path`` is empty.
        """
        if not folder_path:
            return None
        # Split into clean path segments, dropping leading/trailing/empty parts.
        segments = [segment for segment in folder_path.strip("/").split("/") if segment]
        if not segments:
            return None
        current_path = "/"
        folder: AgentFolder | None = None
        # Walk the hierarchy from the root, building each segment's absolute
        # path and creating any missing intermediate folder along the way.
        for segment in segments:
            current_path = f"{current_path}{segment}/"
            folder = await workspace_service.session.scalar(
                select(AgentFolder).where(
                    AgentFolder.workspace_id == workspace_service.workspace_id,
                    AgentFolder.path == current_path,
                )
            )
            # Create the segment folder when it doesn't exist yet.
            if folder is None:
                folder = AgentFolder(
                    workspace_id=workspace_service.workspace_id,
                    name=segment,
                    path=current_path,
                )
                workspace_service.session.add(folder)
                # Flush so the next segment can be parented under this row.
                await workspace_service.session.flush()
        # Loop exits with `folder` bound to the leaf folder.
        return folder

    async def _replace_agent_tags(
        self,
        workspace_service: SyncMappingService,
        preset: AgentPreset,
        tag_names: list[str],
    ) -> None:
        """Replace ``preset``'s tag links to match ``tag_names`` exactly.

        Drops existing links, then upserts each tag by its slugified ref and
        re-links it, so tags are deduplicated and reused across presets.
        """
        # Drop all current links first so the rebuilt set is exact, not additive.
        await workspace_service.session.execute(
            sa.delete(AgentTagLink).where(AgentTagLink.preset_id == preset.id)
        )
        tag_ids: list[uuid.UUID] = []
        # Dedupe (preserving order) and upsert each tag by its slugified ref so
        # tags are shared/reused across presets rather than duplicated.
        for name in sorted(dict.fromkeys(tag_names)):
            ref = slugify(name, separator="-") or name
            tag = await workspace_service.session.scalar(
                select(AgentTag).where(
                    AgentTag.workspace_id == workspace_service.workspace_id,
                    AgentTag.ref == ref,
                )
            )
            # Create the tag when new; otherwise refresh its display name.
            if tag is None:
                tag = AgentTag(
                    workspace_id=workspace_service.workspace_id,
                    name=name,
                    ref=ref,
                )
            else:
                tag.name = name
            workspace_service.session.add(tag)
            # Flush to obtain the tag id needed for the link rows below.
            await workspace_service.session.flush()
            tag_ids.append(tag.id)
        # Re-link the preset to each resolved tag.
        for tag_id in tag_ids:
            workspace_service.session.add(
                AgentTagLink(tag_id=tag_id, preset_id=preset.id)
            )
        await workspace_service.session.flush()

    async def _resolved_subagents_config(
        self,
        workspace_service: SyncMappingService,
        spec: AgentPresetResourceSpec,
    ) -> ResolvedAgentsConfig:
        """Resolve ``spec`` subagents for canonical edges and rollback shadows.

        Resolves each child head to its current version for the rollback shadow.
        """
        if not spec.subagents:
            return ResolvedAgentsConfig()

        subagents: list[ResolvedAttachedSubagentRef] = []
        for subagent in spec.subagents:
            target = await self._resolved_subagent_target(workspace_service, subagent)
            if target is None:
                raise TracecatValidationError(
                    f"Subagent preset {subagent.slug!r} is missing or unpublished"
                )
            child, version = target
            subagents.append(
                ResolvedAttachedSubagentRef(
                    preset=child.slug,
                    preset_id=child.id,
                    preset_version_id=version.id,
                    preset_version=version.version,
                    name=subagent.name,
                    description=subagent.description,
                    max_turns=subagent.max_turns,
                )
            )
        return ResolvedAgentsConfig(subagents=subagents)

    async def _resolved_subagent_target(
        self,
        workspace_service: SyncMappingService,
        subagent: AgentPresetSubagentRef,
    ) -> tuple[AgentPreset, AgentPresetVersion] | None:
        """Resolve a subagent ref to its child preset and desired version."""
        # Look up the child preset by slug within the same workspace. Soft-deleted
        # presets keep their slug, so exclude them to avoid binding a deleted
        # child that runtime resolution would reject.
        child = await workspace_service.session.scalar(
            select(AgentPreset).where(
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.slug == subagent.slug,
                AgentPreset.deleted_at.is_(None),
            )
        )
        if child is None:
            return None

        if child.current_version_id is None:
            return None
        stmt = select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == workspace_service.workspace_id,
            AgentPresetVersion.preset_id == child.id,
            AgentPresetVersion.id == child.current_version_id,
        )

        # Treat an unresolvable version (e.g. requested number not found) as a
        # skip rather than an error.
        version = await workspace_service.session.scalar(stmt)
        if version is None:
            return None
        return child, version

    def _version_attrs_from_spec(
        self,
        spec: AgentPresetResourceSpec,
    ) -> dict[str, Any]:
        """Return publishable head attributes from a resource spec."""
        return {
            "instructions": spec.instructions,
            "model_name": spec.model_name or DEFAULT_AGENT_MODEL_NAME,
            "model_provider": spec.model_provider or DEFAULT_AGENT_MODEL_PROVIDER,
            "catalog_id": spec.catalog_id,
            "base_url": spec.base_url,
            "output_type": spec.output_type,
            "actions": spec.actions or None,
            "namespaces": spec.namespaces or None,
            "tool_approvals": _tool_approvals(spec.tool_approvals),
            "mcp_integrations": spec.mcp_integrations or None,
            "retries": spec.retries,
            "enable_thinking": spec.enable_thinking,
            "enable_internet_access": spec.enable_internet_access,
        }

    async def _skill_binding_targets_for_spec(
        self,
        workspace_service: SyncMappingService,
        spec: AgentPresetResourceSpec,
    ) -> list[tuple[Skill, SkillVersion]]:
        """Resolve ``spec``'s skill bindings to ``(skill, version)`` pairs.

        Skips any binding whose skill or requested version is missing from the
        workspace.
        """
        targets: list[tuple[Skill, SkillVersion]] = []
        for binding in spec.skills:
            skill, skill_version = await self._skill_binding_targets(
                workspace_service, binding
            )
            if skill is None or skill_version is None:
                raise TracecatValidationError(
                    f"Skill {binding.slug!r} is missing or unpublished"
                )
            targets.append((skill, skill_version))
        return targets

    async def _skill_binding_targets(
        self,
        workspace_service: SyncMappingService,
        binding: AgentPresetSkillBinding,
    ) -> tuple[Skill | None, SkillVersion | None]:
        """Resolve one Skill-head binding to its current version shadow."""
        # Resolve by stable slug, with the expand-window name fallback.
        skill = await workspace_service.session.scalar(
            select(Skill).where(
                Skill.workspace_id == workspace_service.workspace_id,
                sa.or_(
                    Skill.slug == binding.slug,
                    sa.and_(Skill.slug.is_(None), Skill.name == binding.slug),
                ),
                # Expand-window check: legacy writers set only archived_at; the
                # contract release drops the archived_at leg.
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
        )
        if skill is None:
            return None, None
        stmt = select(SkillVersion).where(
            SkillVersion.workspace_id == workspace_service.workspace_id,
            SkillVersion.skill_id == skill.id,
            SkillVersion.id == skill.current_version_id,
        )
        version = await workspace_service.session.scalar(stmt)
        return skill, version


def _tool_approvals(value: dict[str, Any]) -> dict[str, bool] | None:
    """Normalize tool approval values into a ``tool -> bool`` map.

    Treats the legacy ``"manual"`` marker and ``True`` as approval-required.
    Returns ``None`` for an empty mapping so the column stays unset.
    """
    # An empty map leaves the column unset (None) rather than storing {}.
    if not value:
        return None
    # Legacy "manual" marker and bare True both mean approval-required.
    return {
        key: bool(raw_value == "manual" or raw_value is True)
        for key, raw_value in value.items()
    }
