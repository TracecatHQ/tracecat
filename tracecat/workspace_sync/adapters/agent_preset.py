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

from tracecat.agent.subagents import AgentSubagentsConfig
from tracecat.db.models import (
    AgentFolder,
    AgentPreset,
    AgentPresetSkill,
    AgentPresetVersion,
    AgentPresetVersionSkill,
    AgentTag,
    AgentTagLink,
    Skill,
    SkillVersion,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    CompoundYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    AGENT_PRESET_ROOT,
    AgentPresetResourceSpec,
    AgentPresetSkillBinding,
    AgentPresetSubagentRef,
    WorkspaceSpec,
)

AGENT_PRESET_FILENAME = "preset.yml"
DEFAULT_AGENT_MODEL_NAME = "gpt-4.1-mini"
DEFAULT_AGENT_MODEL_PROVIDER = "openai"


class AgentPresetAdapter(CompoundYamlAdapter):
    """Adapter for agent presets, their versions, tags, and skill bindings."""

    resource_type = SyncResourceType.AGENT_PRESET
    spec_attr = "agent_presets"
    model = AgentPresetResourceSpec
    root = AGENT_PRESET_ROOT
    filename = AGENT_PRESET_FILENAME

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project agent presets into Git specs."""
        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == workspace_service.workspace_id)
            .options(
                selectinload(AgentPreset.folder),
                selectinload(AgentPreset.tags),
                selectinload(AgentPreset.skill_bindings).selectinload(
                    AgentPresetSkill.skill
                ),
                selectinload(AgentPreset.skill_bindings).selectinload(
                    AgentPresetSkill.skill_version
                ),
            )
            .order_by(AgentPreset.slug.asc(), AgentPreset.id.asc())
        )
        presets = list((await workspace_service.session.execute(stmt)).scalars().all())
        source_ids_by_local_id = await self.source_ids_by_local_id(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for preset in presets:
            source_id = source_ids_by_local_id.get(preset.id)
            if source_id is None:
                source_id = unique_source_id(preset.slug, reserved=reserved)
            reserved.add(source_id)
            skill_bindings = [
                AgentPresetSkillBinding(
                    slug=binding.skill.name,
                    version=binding.skill_version.version,
                )
                for binding in sorted(
                    preset.skill_bindings or [],
                    key=lambda item: item.skill.name,
                )
                if binding.skill is not None and binding.skill_version is not None
            ]
            specs[source_id] = AgentPresetResourceSpec(
                id=source_id,
                slug=preset.slug,
                name=preset.name,
                folder_path=preset.folder.path if preset.folder else None,
                tags=sorted(tag.name for tag in preset.tags),
                instructions=preset.instructions,
                tool_approvals=preset.tool_approvals or {},
                actions=sorted(preset.actions or []),
                skills=skill_bindings,
                subagents=_subagent_refs(preset.agents),
                catalog_id=preset.catalog_id,
                model_name=preset.model_name,
                model_provider=preset.model_provider,
                base_url=preset.base_url,
                output_type=preset.output_type,
                namespaces=sorted(preset.namespaces or []),
                mcp_integrations=sorted(preset.mcp_integrations or []),
                retries=preset.retries,
                enable_thinking=preset.enable_thinking,
                enable_internet_access=preset.enable_internet_access,
            )
            resources.append(self.projected_resource(source_id, preset.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile agent preset specs into the local database.

        Imports presets in two passes: the first upserts each preset, folder,
        and tag set; the second resolves subagent references and skill bindings
        in topological order and pins each preset's current version.
        """
        presets = workspace_spec.agent_presets
        import_order = self._preset_import_order(presets)
        imported: list[ImportedResource] = []
        preset_by_source_id: dict[str, AgentPreset] = {}
        for source_id, spec in sorted(presets.items()):
            preset = await self._preset_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
            )
            if preset is None:
                preset = AgentPreset(
                    workspace_id=workspace_service.workspace_id,
                    slug=spec.slug,
                    name=spec.name,
                    model_name=spec.model_name or DEFAULT_AGENT_MODEL_NAME,
                    model_provider=spec.model_provider or DEFAULT_AGENT_MODEL_PROVIDER,
                    agents=AgentSubagentsConfig().model_dump(mode="json"),
                )
            else:
                preset.slug = spec.slug
            self._apply_preset_spec(preset, spec)
            folder = await self._ensure_agent_folder(
                workspace_service, spec.folder_path
            )
            preset.folder_id = folder.id if folder is not None else None
            workspace_service.session.add(preset)
            await workspace_service.session.flush()
            await self._replace_agent_tags(workspace_service, preset, spec.tags)
            preset_by_source_id[source_id] = preset

        for source_id in import_order:
            spec = presets[source_id]
            preset = preset_by_source_id[source_id]
            preset.agents = await self._resolved_subagents_config(
                workspace_service, spec
            )
            workspace_service.session.add(preset)
            await workspace_service.session.flush()
            skill_targets = await self._skill_binding_targets_for_spec(
                workspace_service, spec
            )
            current_version = await self._current_version_for_preset(
                workspace_service, preset
            )
            if current_version is not None and await self._version_matches_preset(
                workspace_service,
                current_version,
                preset,
                skill_targets,
            ):
                version = current_version
            else:
                version = await self._create_agent_preset_version(
                    workspace_service, preset
                )
                await self._replace_version_skill_bindings(
                    workspace_service, version, skill_targets
                )
            await self._replace_head_skill_bindings(
                workspace_service, preset, skill_targets
            )
            preset.current_version_id = version.id
            workspace_service.session.add(preset)
            await workspace_service.session.flush()
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
        slug_to_source_id: dict[str, str] = {}
        for source_id, spec in sorted(presets.items()):
            if spec.slug in slug_to_source_id:
                raise TracecatValidationError(
                    f"Agent preset sync specs must have unique slugs: {spec.slug!r}"
                )
            slug_to_source_id[spec.slug] = source_id

        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[str] = []

        def visit(source_id: str) -> None:
            """Depth-first visit a preset and append it after its subagents."""
            if source_id in visited:
                return
            if source_id in visiting:
                raise TracecatValidationError(
                    "Cyclic agent preset subagent reference detected during import"
                )
            visiting.add(source_id)
            spec = presets[source_id]
            for subagent in sorted(spec.subagents, key=lambda item: item.slug):
                if child_source_id := slug_to_source_id.get(subagent.slug):
                    visit(child_source_id)
            visiting.remove(source_id)
            visited.add(source_id)
            ordered.append(source_id)

        for source_id in sorted(presets):
            visit(source_id)
        return ordered

    def _apply_preset_spec(
        self,
        preset: AgentPreset,
        spec: AgentPresetResourceSpec,
    ) -> None:
        """Copy scalar spec fields onto ``preset``.

        Always applies core fields; optional fields are only overwritten when
        the spec explicitly set them (see :func:`_field_was_set`), so omitted
        keys preserve existing preset values.
        """
        preset.name = spec.name
        preset.instructions = spec.instructions
        preset.actions = spec.actions or None
        preset.tool_approvals = _tool_approvals(spec.tool_approvals)
        if _field_was_set(spec, "catalog_id"):
            preset.catalog_id = spec.catalog_id
        preset.model_name = spec.model_name or preset.model_name
        preset.model_provider = spec.model_provider or preset.model_provider
        if _field_was_set(spec, "base_url"):
            preset.base_url = spec.base_url
        if _field_was_set(spec, "output_type"):
            preset.output_type = spec.output_type
        if _field_was_set(spec, "namespaces"):
            preset.namespaces = spec.namespaces or None
        if _field_was_set(spec, "mcp_integrations"):
            preset.mcp_integrations = spec.mcp_integrations or None
        if _field_was_set(spec, "retries"):
            preset.retries = spec.retries
        if _field_was_set(spec, "enable_thinking"):
            preset.enable_thinking = spec.enable_thinking
        if _field_was_set(spec, "enable_internet_access"):
            preset.enable_internet_access = spec.enable_internet_access

    async def _preset_for_import(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        spec: AgentPresetResourceSpec,
    ) -> AgentPreset | None:
        """Resolve the existing preset to update for ``source_id``, if any.

        Prefers the preset already mapped to ``source_id`` (validating the slug
        is still free), then falls back to matching on slug. Returns ``None``
        when no preset exists and a new one must be created.
        """
        preset = await self._preset_by_source_id(workspace_service, source_id=source_id)
        if preset is not None:
            await self._ensure_slug_available(
                workspace_service,
                source_id=source_id,
                slug=spec.slug,
                preset_id=preset.id,
            )
            return preset

        return await workspace_service.session.scalar(
            select(AgentPreset)
            .where(
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.slug == spec.slug,
            )
            .options(selectinload(AgentPreset.tags))
        )

    async def _preset_by_source_id(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> AgentPreset | None:
        """Load the preset mapped to ``source_id`` via the sync mapping, if any."""
        local_id = await self.local_id_for_source_id(workspace_service, source_id)
        if local_id is None:
            return None

        return await workspace_service.session.scalar(
            select(AgentPreset)
            .where(
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.id == local_id,
            )
            .options(selectinload(AgentPreset.tags))
        )

    async def _ensure_slug_available(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        slug: str,
        preset_id: uuid.UUID,
    ) -> None:
        """Guard that ``slug`` is not already used by a different preset.

        Raises :class:`TracecatValidationError` when another preset in the
        workspace owns ``slug``.
        """
        conflict_id = await workspace_service.session.scalar(
            select(AgentPreset.id).where(
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.slug == slug,
                AgentPreset.id != preset_id,
            )
        )
        if conflict_id is None:
            return

        raise TracecatValidationError(
            f"Agent preset sync source id {source_id!r} cannot use slug {slug!r} "
            "because another preset already uses that slug."
        )

    async def _ensure_agent_folder(
        self,
        workspace_service: BaseWorkspaceService,
        folder_path: str | None,
    ) -> AgentFolder | None:
        """Resolve ``folder_path`` to an :class:`AgentFolder`, creating segments.

        Walks each ``/``-separated segment, creating any missing intermediate
        folders, and returns the leaf folder. Returns ``None`` when
        ``folder_path`` is empty.
        """
        if not folder_path:
            return None
        segments = [segment for segment in folder_path.strip("/").split("/") if segment]
        if not segments:
            return None
        current_path = "/"
        folder: AgentFolder | None = None
        for segment in segments:
            current_path = f"{current_path}{segment}/"
            folder = await workspace_service.session.scalar(
                select(AgentFolder).where(
                    AgentFolder.workspace_id == workspace_service.workspace_id,
                    AgentFolder.path == current_path,
                )
            )
            if folder is None:
                folder = AgentFolder(
                    workspace_id=workspace_service.workspace_id,
                    name=segment,
                    path=current_path,
                )
                workspace_service.session.add(folder)
                await workspace_service.session.flush()
        return folder

    async def _replace_agent_tags(
        self,
        workspace_service: BaseWorkspaceService,
        preset: AgentPreset,
        tag_names: list[str],
    ) -> None:
        """Replace ``preset``'s tag links to match ``tag_names`` exactly.

        Drops existing links, then upserts each tag by its slugified ref and
        re-links it, so tags are deduplicated and reused across presets.
        """
        await workspace_service.session.execute(
            sa.delete(AgentTagLink).where(AgentTagLink.preset_id == preset.id)
        )
        tag_ids: list[uuid.UUID] = []
        for name in sorted(dict.fromkeys(tag_names)):
            ref = slugify(name, separator="-") or name
            tag = await workspace_service.session.scalar(
                select(AgentTag).where(
                    AgentTag.workspace_id == workspace_service.workspace_id,
                    AgentTag.ref == ref,
                )
            )
            if tag is None:
                tag = AgentTag(
                    workspace_id=workspace_service.workspace_id,
                    name=name,
                    ref=ref,
                )
            else:
                tag.name = name
            workspace_service.session.add(tag)
            await workspace_service.session.flush()
            tag_ids.append(tag.id)
        for tag_id in tag_ids:
            workspace_service.session.add(
                AgentTagLink(tag_id=tag_id, preset_id=preset.id)
            )
        await workspace_service.session.flush()

    async def _resolved_subagents_config(
        self,
        workspace_service: BaseWorkspaceService,
        spec: AgentPresetResourceSpec,
    ) -> dict[str, Any]:
        """Build the subagents config dict for ``spec``.

        Resolves each subagent reference to its current preset version, skipping
        any that are missing or unpublished. Returns the default disabled config
        when the spec declares no subagents.
        """
        if not spec.subagents:
            return AgentSubagentsConfig().model_dump(mode="json")

        subagents: list[dict[str, Any]] = []
        for subagent in spec.subagents:
            child = await workspace_service.session.scalar(
                select(AgentPreset).where(
                    AgentPreset.workspace_id == workspace_service.workspace_id,
                    AgentPreset.slug == subagent.slug,
                )
            )
            if child is None or child.current_version_id is None:
                continue
            subagents.append(
                {
                    "preset": child.slug,
                    "preset_id": str(child.id),
                    "preset_version_id": str(child.current_version_id),
                    "preset_version": None,
                    "name": None,
                    "description": None,
                    "max_turns": None,
                }
            )
        return {"enabled": bool(subagents), "subagents": subagents}

    async def _current_version_for_preset(
        self,
        workspace_service: BaseWorkspaceService,
        preset: AgentPreset,
    ) -> AgentPresetVersion | None:
        """Return ``preset``'s current :class:`AgentPresetVersion`, if pinned."""
        if preset.current_version_id is None:
            return None
        return await workspace_service.session.scalar(
            select(AgentPresetVersion).where(
                AgentPresetVersion.workspace_id == workspace_service.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
                AgentPresetVersion.id == preset.current_version_id,
            )
        )

    async def _version_matches_preset(
        self,
        workspace_service: BaseWorkspaceService,
        version: AgentPresetVersion,
        preset: AgentPreset,
        skill_targets: list[tuple[Skill, SkillVersion]],
    ) -> bool:
        """Return whether ``version`` already captures ``preset``'s state.

        Compares every versioned attribute and the version's skill bindings
        against the preset, so a matching version can be reused instead of
        cutting a new one.
        """
        for key, value in self._version_attrs_from_preset(preset).items():
            if getattr(version, key) != value:
                return False
        desired_skill_targets = {
            (skill.id, skill_version.id) for skill, skill_version in skill_targets
        }
        existing_skill_targets = {
            (skill_id, skill_version_id)
            for skill_id, skill_version_id in (
                await workspace_service.session.execute(
                    select(
                        AgentPresetVersionSkill.skill_id,
                        AgentPresetVersionSkill.skill_version_id,
                    ).where(
                        AgentPresetVersionSkill.workspace_id
                        == workspace_service.workspace_id,
                        AgentPresetVersionSkill.preset_version_id == version.id,
                    )
                )
            ).tuples()
        }
        return existing_skill_targets == desired_skill_targets

    async def _create_agent_preset_version(
        self,
        workspace_service: BaseWorkspaceService,
        preset: AgentPreset,
    ) -> AgentPresetVersion:
        """Create the next :class:`AgentPresetVersion` snapshotting ``preset``.

        Increments the version number past the preset's highest existing version
        and copies the current preset attributes onto the new row.
        """
        current_version = await workspace_service.session.scalar(
            select(sa.func.max(AgentPresetVersion.version)).where(
                AgentPresetVersion.workspace_id == workspace_service.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
            )
        )
        version = AgentPresetVersion(
            workspace_id=workspace_service.workspace_id,
            preset_id=preset.id,
            version=(current_version or 0) + 1,
            **self._version_attrs_from_preset(preset),
        )
        workspace_service.session.add(version)
        await workspace_service.session.flush()
        return version

    def _version_attrs_from_preset(self, preset: AgentPreset) -> dict[str, Any]:
        """Return the versioned preset attributes to snapshot or compare."""
        return {
            "instructions": preset.instructions,
            "model_name": preset.model_name,
            "model_provider": preset.model_provider,
            "catalog_id": preset.catalog_id,
            "base_url": preset.base_url,
            "output_type": preset.output_type,
            "actions": preset.actions,
            "namespaces": preset.namespaces,
            "tool_approvals": preset.tool_approvals,
            "mcp_integrations": preset.mcp_integrations,
            "agents": preset.agents,
            "retries": preset.retries,
            "enable_thinking": preset.enable_thinking,
            "enable_internet_access": preset.enable_internet_access,
        }

    async def _replace_head_skill_bindings(
        self,
        workspace_service: BaseWorkspaceService,
        preset: AgentPreset,
        skill_targets: list[tuple[Skill, SkillVersion]],
    ) -> None:
        """Replace ``preset``'s head skill bindings with ``skill_targets``."""
        await workspace_service.session.execute(
            sa.delete(AgentPresetSkill).where(
                AgentPresetSkill.workspace_id == workspace_service.workspace_id,
                AgentPresetSkill.preset_id == preset.id,
            )
        )
        for skill, skill_version in skill_targets:
            workspace_service.session.add(
                AgentPresetSkill(
                    workspace_id=workspace_service.workspace_id,
                    preset_id=preset.id,
                    skill_id=skill.id,
                    skill_version_id=skill_version.id,
                )
            )
        await workspace_service.session.flush()

    async def _replace_version_skill_bindings(
        self,
        workspace_service: BaseWorkspaceService,
        version: AgentPresetVersion,
        skill_targets: list[tuple[Skill, SkillVersion]],
    ) -> None:
        """Replace ``version``'s skill bindings with ``skill_targets``."""
        await workspace_service.session.execute(
            sa.delete(AgentPresetVersionSkill).where(
                AgentPresetVersionSkill.workspace_id == workspace_service.workspace_id,
                AgentPresetVersionSkill.preset_version_id == version.id,
            )
        )
        for skill, skill_version in skill_targets:
            workspace_service.session.add(
                AgentPresetVersionSkill(
                    workspace_id=workspace_service.workspace_id,
                    preset_version_id=version.id,
                    skill_id=skill.id,
                    skill_version_id=skill_version.id,
                )
            )
        await workspace_service.session.flush()

    async def _skill_binding_targets_for_spec(
        self,
        workspace_service: BaseWorkspaceService,
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
                continue
            targets.append((skill, skill_version))
        return targets

    async def _skill_binding_targets(
        self,
        workspace_service: BaseWorkspaceService,
        binding: AgentPresetSkillBinding,
    ) -> tuple[Skill | None, SkillVersion | None]:
        """Resolve one skill binding to its ``(skill, version)`` pair.

        Looks up the skill by slug, then the requested version (or the skill's
        current version when ``binding.version`` is unset). Returns
        ``(None, None)`` when the skill or version cannot be found.
        """
        skill = await workspace_service.session.scalar(
            select(Skill).where(
                Skill.workspace_id == workspace_service.workspace_id,
                Skill.name == binding.slug,
            )
        )
        if skill is None:
            return None, None
        version_number = binding.version
        stmt = select(SkillVersion).where(
            SkillVersion.workspace_id == workspace_service.workspace_id,
            SkillVersion.skill_id == skill.id,
        )
        if version_number is not None:
            stmt = stmt.where(SkillVersion.version == version_number)
        else:
            stmt = stmt.where(SkillVersion.id == skill.current_version_id)
        version = await workspace_service.session.scalar(stmt)
        return skill, version


def _subagent_refs(agents: dict[str, Any]) -> list[AgentPresetSubagentRef]:
    """Extract slug-only subagent refs from a preset's ``agents`` config.

    Returns an empty list when the config is missing or fails to validate.
    """
    try:
        config = AgentSubagentsConfig.model_validate(agents or {"enabled": False})
    except Exception:
        return []
    return [
        AgentPresetSubagentRef(slug=subagent.preset)
        for subagent in sorted(config.subagents, key=lambda item: item.preset)
    ]


def _tool_approvals(value: dict[str, Any]) -> dict[str, bool] | None:
    """Normalize tool approval values into a ``tool -> bool`` map.

    Treats the legacy ``"manual"`` marker and ``True`` as approval-required.
    Returns ``None`` for an empty mapping so the column stays unset.
    """
    if not value:
        return None
    return {
        key: bool(raw_value == "manual" or raw_value is True)
        for key, raw_value in value.items()
    }


def _field_was_set(spec: BaseModel, field_name: str) -> bool:
    """Return whether ``field_name`` was explicitly provided on ``spec``."""
    return field_name in spec.model_fields_set
