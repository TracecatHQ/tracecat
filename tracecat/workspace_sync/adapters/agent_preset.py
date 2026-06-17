"""Agent preset resource adapter (presets, versions, tags, skill bindings)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

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
)

AGENT_PRESET_FILENAME = "preset.yml"
DEFAULT_AGENT_MODEL_NAME = "gpt-4.1-mini"
DEFAULT_AGENT_MODEL_PROVIDER = "openai"


class AgentPresetAdapter(CompoundYamlAdapter):
    resource_type = SyncResourceType.AGENT_PRESET
    spec_attr = "agent_presets"
    model = AgentPresetResourceSpec
    root = AGENT_PRESET_ROOT
    filename = AGENT_PRESET_FILENAME

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == ctx.workspace_id)
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
        presets = list((await ctx.session.execute(stmt)).scalars().all())
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for preset in presets:
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
            specs[source_id] = AgentPresetResourceSpec.model_validate(
                {
                    "id": source_id,
                    "slug": preset.slug,
                    "name": preset.name,
                    "folder_path": preset.folder.path if preset.folder else None,
                    "tags": sorted(tag.name for tag in preset.tags),
                    "instructions": preset.instructions,
                    "tool_approvals": preset.tool_approvals or {},
                    "actions": sorted(preset.actions or []),
                    "skills": skill_bindings,
                    "subagents": _subagent_refs(preset.agents),
                    "model_name": preset.model_name,
                    "model_provider": preset.model_provider,
                    "base_url": preset.base_url,
                    "output_type": preset.output_type,
                    "namespaces": sorted(preset.namespaces or []),
                    "mcp_integrations": sorted(preset.mcp_integrations or []),
                    "retries": preset.retries,
                    "enable_thinking": preset.enable_thinking,
                    "enable_internet_access": preset.enable_internet_access,
                }
            )
            resources.append(self.projected_resource(source_id, preset.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        presets = cast(Mapping[str, AgentPresetResourceSpec], specs)
        imported: list[ImportedResource] = []
        preset_by_slug: dict[str, AgentPreset] = {}
        for _source_id, spec in sorted(presets.items()):
            preset = await ctx.session.scalar(
                select(AgentPreset)
                .where(
                    AgentPreset.workspace_id == ctx.workspace_id,
                    AgentPreset.slug == spec.slug,
                )
                .options(selectinload(AgentPreset.tags))
            )
            if preset is None:
                preset = AgentPreset(
                    workspace_id=ctx.workspace_id,
                    slug=spec.slug,
                    name=spec.name,
                    model_name=getattr(spec, "model_name", None)
                    or DEFAULT_AGENT_MODEL_NAME,
                    model_provider=getattr(spec, "model_provider", None)
                    or DEFAULT_AGENT_MODEL_PROVIDER,
                    agents=AgentSubagentsConfig().model_dump(mode="json"),
                )
            preset.name = spec.name
            preset.instructions = spec.instructions
            preset.actions = spec.actions or None
            preset.tool_approvals = _tool_approvals(spec.tool_approvals)
            preset.model_name = getattr(spec, "model_name", None) or preset.model_name
            preset.model_provider = (
                getattr(spec, "model_provider", None) or preset.model_provider
            )
            folder = await self._ensure_agent_folder(ctx, spec.folder_path)
            preset.folder_id = folder.id if folder is not None else None
            ctx.session.add(preset)
            await ctx.session.flush()
            await self._replace_agent_tags(ctx, preset, spec.tags)
            preset_by_slug[spec.slug] = preset

        for source_id, spec in sorted(presets.items()):
            preset = preset_by_slug[spec.slug]
            preset.agents = await self._resolved_subagents_config(ctx, spec)
            ctx.session.add(preset)
            await ctx.session.flush()
            version = await self._upsert_agent_preset_version(ctx, preset)
            await self._replace_preset_skill_bindings(ctx, preset, version, spec)
            preset.current_version_id = version.id
            ctx.session.add(preset)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, preset.id))
        return imported

    async def _ensure_agent_folder(
        self,
        ctx: BaseWorkspaceService,
        folder_path: str | None,
    ) -> AgentFolder | None:
        if not folder_path:
            return None
        segments = [segment for segment in folder_path.strip("/").split("/") if segment]
        if not segments:
            return None
        current_path = "/"
        folder: AgentFolder | None = None
        for segment in segments:
            current_path = f"{current_path}{segment}/"
            folder = await ctx.session.scalar(
                select(AgentFolder).where(
                    AgentFolder.workspace_id == ctx.workspace_id,
                    AgentFolder.path == current_path,
                )
            )
            if folder is None:
                folder = AgentFolder(
                    workspace_id=ctx.workspace_id,
                    name=segment,
                    path=current_path,
                )
                ctx.session.add(folder)
                await ctx.session.flush()
        return folder

    async def _replace_agent_tags(
        self,
        ctx: BaseWorkspaceService,
        preset: AgentPreset,
        tag_names: list[str],
    ) -> None:
        await ctx.session.execute(
            sa.delete(AgentTagLink).where(AgentTagLink.preset_id == preset.id)
        )
        tag_ids: list[uuid.UUID] = []
        for name in sorted(dict.fromkeys(tag_names)):
            ref = slugify(name, separator="-") or name
            tag = await ctx.session.scalar(
                select(AgentTag).where(
                    AgentTag.workspace_id == ctx.workspace_id,
                    AgentTag.ref == ref,
                )
            )
            if tag is None:
                tag = AgentTag(
                    workspace_id=ctx.workspace_id,
                    name=name,
                    ref=ref,
                )
            else:
                tag.name = name
            ctx.session.add(tag)
            await ctx.session.flush()
            tag_ids.append(tag.id)
        for tag_id in tag_ids:
            ctx.session.add(AgentTagLink(tag_id=tag_id, preset_id=preset.id))
        await ctx.session.flush()

    async def _resolved_subagents_config(
        self,
        ctx: BaseWorkspaceService,
        spec: AgentPresetResourceSpec,
    ) -> dict[str, Any]:
        if not spec.subagents:
            return AgentSubagentsConfig().model_dump(mode="json")

        subagents: list[dict[str, Any]] = []
        for subagent in spec.subagents:
            child = await ctx.session.scalar(
                select(AgentPreset).where(
                    AgentPreset.workspace_id == ctx.workspace_id,
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

    async def _upsert_agent_preset_version(
        self,
        ctx: BaseWorkspaceService,
        preset: AgentPreset,
    ) -> AgentPresetVersion:
        version = None
        if preset.current_version_id is not None:
            version = await ctx.session.scalar(
                select(AgentPresetVersion).where(
                    AgentPresetVersion.workspace_id == ctx.workspace_id,
                    AgentPresetVersion.preset_id == preset.id,
                    AgentPresetVersion.id == preset.current_version_id,
                )
            )
        if version is None:
            version = AgentPresetVersion(
                workspace_id=ctx.workspace_id,
                preset_id=preset.id,
                version=1,
            )
        attrs = {
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
        for key, value in attrs.items():
            setattr(version, key, value)
        ctx.session.add(version)
        await ctx.session.flush()
        return version

    async def _replace_preset_skill_bindings(
        self,
        ctx: BaseWorkspaceService,
        preset: AgentPreset,
        version: AgentPresetVersion,
        spec: AgentPresetResourceSpec,
    ) -> None:
        await ctx.session.execute(
            sa.delete(AgentPresetSkill).where(
                AgentPresetSkill.workspace_id == ctx.workspace_id,
                AgentPresetSkill.preset_id == preset.id,
            )
        )
        await ctx.session.execute(
            sa.delete(AgentPresetVersionSkill).where(
                AgentPresetVersionSkill.workspace_id == ctx.workspace_id,
                AgentPresetVersionSkill.preset_version_id == version.id,
            )
        )
        for binding in spec.skills:
            skill, skill_version = await self._skill_binding_targets(ctx, binding)
            if skill is None or skill_version is None:
                continue
            ctx.session.add(
                AgentPresetSkill(
                    workspace_id=ctx.workspace_id,
                    preset_id=preset.id,
                    skill_id=skill.id,
                    skill_version_id=skill_version.id,
                )
            )
            ctx.session.add(
                AgentPresetVersionSkill(
                    workspace_id=ctx.workspace_id,
                    preset_version_id=version.id,
                    skill_id=skill.id,
                    skill_version_id=skill_version.id,
                )
            )
        await ctx.session.flush()

    async def _skill_binding_targets(
        self,
        ctx: BaseWorkspaceService,
        binding: AgentPresetSkillBinding,
    ) -> tuple[Skill | None, SkillVersion | None]:
        skill = await ctx.session.scalar(
            select(Skill).where(
                Skill.workspace_id == ctx.workspace_id,
                Skill.name == binding.slug,
            )
        )
        if skill is None:
            return None, None
        version_number = binding.version
        stmt = select(SkillVersion).where(
            SkillVersion.workspace_id == ctx.workspace_id,
            SkillVersion.skill_id == skill.id,
        )
        if version_number is not None:
            stmt = stmt.where(SkillVersion.version == version_number)
        else:
            stmt = stmt.where(SkillVersion.id == skill.current_version_id)
        version = await ctx.session.scalar(stmt)
        return skill, version


def _subagent_refs(agents: dict[str, Any]) -> list[AgentPresetSubagentRef]:
    try:
        config = AgentSubagentsConfig.model_validate(agents or {"enabled": False})
    except Exception:
        return []
    return [
        AgentPresetSubagentRef(slug=subagent.preset)
        for subagent in sorted(config.subagents, key=lambda item: item.preset)
    ]


def _tool_approvals(value: dict[str, Any]) -> dict[str, bool] | None:
    if not value:
        return None
    return {
        key: bool(raw_value == "manual" or raw_value is True)
        for key, raw_value in value.items()
    }
