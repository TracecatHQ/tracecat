"""Agent preset resource adapter (presets, versions, tags, skill bindings)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

import sqlalchemy as sa
import yaml
from pydantic import BaseModel, ValidationError
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.preset.schemas import AGENT_PRESET_EXECUTION_FIELDS
from tracecat.agent.skill.service import SkillService
from tracecat.agent.subagents import (
    AgentSubagentsConfig,
    AnyAttachedSubagentRef,
    HeadAttachedSubagentRef,
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
)
from tracecat.db.models import (
    AgentFolder,
    AgentPreset,
    AgentPresetSkill,
    AgentPresetVersion,
    AgentPresetVersionSkill,
    AgentPresetVersionSubagent,
    AgentTag,
    AgentTagLink,
    Skill,
)
from tracecat.db.soft_delete import with_deleted
from tracecat.exceptions import TracecatValidationError
from tracecat.sync import PullDiagnostic, serializable_validation_errors
from tracecat.workspace_sync.adapters.base import (
    DirectoryManifestAdapter,
    ImportedResource,
    NameSwapPlan,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
    SyncMappingService,
    path_parts,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    AGENT_PRESET_ROOT,
    AgentPresetResourceSpec,
    AgentPresetSkillBinding,
    AgentPresetSubagentRef,
    AgentPresetVersionResourceSpec,
    WorkspaceManifestResources,
    WorkspaceSpec,
)
from tracecat.workspace_sync.serialization import serialize_yaml_model

AGENT_PRESET_FILENAME = "preset.yml"
AGENT_PRESET_VERSIONS_DIR = "versions"
DEFAULT_AGENT_MODEL_NAME = "gpt-5.5"
DEFAULT_AGENT_MODEL_PROVIDER = "openai"


def _legacy_agents_config(agents: Mapping[str, object] | None) -> AgentSubagentsConfig:
    """Parse old-writer subagent JSON without blocking workspace projection."""

    try:
        return AgentSubagentsConfig.model_validate(agents or {})
    except ValidationError:
        return AgentSubagentsConfig()


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

    def _version_source_path(self, source_id: str, version: int) -> str:
        """Return the repository path for an agent preset version manifest."""
        return f"{self.root}/{source_id}/{AGENT_PRESET_VERSIONS_DIR}/{version}.yml"

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
        """Map ``versions/<n>.yml`` companion files to ``(source_id, relpath)``."""
        parts = path_parts(path)
        root_parts = path_parts(roots.agent_presets)
        if len(parts) != len(root_parts) + 3:
            return None
        if parts[: len(root_parts)] != root_parts:
            return None
        source_id = parts[len(root_parts)]
        versions_dir = parts[len(root_parts) + 1]
        filename = parts[len(root_parts) + 2]
        if not source_id or versions_dir != AGENT_PRESET_VERSIONS_DIR:
            return None
        return (source_id, f"{versions_dir}/{filename}") if filename else None

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize immutable preset versions to companion YAML files."""
        preset = cast(AgentPresetResourceSpec, spec)
        return {
            self._version_source_path(source_id, version_number): serialize_yaml_model(
                version
            )
            for version_number, version in sorted(preset.versions.items())
        }

    def attach_extra_files(
        self,
        specs: dict[str, BaseModel],
        extra_files: Mapping[tuple[str, str], str],
        diagnostics: list[PullDiagnostic],
    ) -> dict[str, BaseModel]:
        """Fold parsed preset version YAML files back into preset specs."""
        version_files_by_source: dict[str, dict[int, str]] = {}
        for (source_id, relpath), content in extra_files.items():
            version_number = _parse_preset_version_relpath(relpath)
            if version_number is None:
                continue
            version_files_by_source.setdefault(source_id, {})[version_number] = content

        updated: dict[str, BaseModel] = {}
        for source_id, base_spec in specs.items():
            spec = cast(AgentPresetResourceSpec, base_spec)
            versions = dict(spec.versions)
            for version_number, content in sorted(
                version_files_by_source.get(source_id, {}).items()
            ):
                version = _parse_agent_preset_version_manifest(
                    self,
                    source_id=source_id,
                    version_number=version_number,
                    content=content,
                    diagnostics=diagnostics,
                )
                if version is not None:
                    versions[version_number] = version
            if (
                spec.current_version is not None
                and spec.current_version not in versions
            ):
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=self.source_path(source_id),
                        workflow_title=spec.name,
                        error_type="dependency",
                        message=(
                            f"Agent preset {spec.slug!r} current version "
                            f"{spec.current_version} is missing"
                        ),
                        details={
                            "preset_slug": spec.slug,
                            "preset_version": spec.current_version,
                        },
                    )
                )
            updated[source_id] = spec.model_copy(update={"versions": versions})
        return updated

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
        versions_by_slug: dict[str, set[int]] = {}
        for slug, version in refs.versioned_slugs:
            versions_by_slug.setdefault(slug, set()).add(version)
        versions_by_preset_id = {
            preset.id: versions_by_slug[preset.slug]
            for preset in presets
            if preset.slug in versions_by_slug
        }
        return await self._projection_from_presets(
            workspace_service,
            presets,
            versions_by_preset_id=versions_by_preset_id,
        )

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
        versions_by_preset_id: Mapping[uuid.UUID, set[int]] | None = None,
    ) -> ResourceProjection:
        """Build sync specs from eager-loaded preset rows."""
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for preset in presets:
            source_id = assigner.assign(preset.id, preset.slug)
            current_version = preset.current_version
            version_numbers = set((versions_by_preset_id or {}).get(preset.id, set()))
            if current_version is not None:
                version_numbers.add(current_version.version)
            versions = await self._version_specs_for_preset(
                workspace_service,
                preset=preset,
                version_numbers=version_numbers,
            )
            specs[source_id] = AgentPresetResourceSpec(
                id=source_id,
                slug=preset.slug,
                name=preset.name,
                current_version=(
                    current_version.version if current_version is not None else None
                ),
                folder_path=preset.folder.path if preset.folder else None,
                tags=sorted(tag.name for tag in preset.tags),
                versions=versions,
            )
            resources.append(self.projected_resource(source_id, preset.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def _version_specs_for_preset(
        self,
        workspace_service: SyncMappingService,
        *,
        preset: AgentPreset,
        version_numbers: set[int],
    ) -> dict[int, AgentPresetVersionResourceSpec]:
        """Build version specs for the requested preset version numbers."""
        if not version_numbers:
            return {}
        stmt = (
            select(AgentPresetVersion)
            .where(
                AgentPresetVersion.workspace_id == workspace_service.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
                AgentPresetVersion.version.in_(version_numbers),
            )
            .order_by(AgentPresetVersion.version.asc())
        )
        version_rows = list((await workspace_service.session.scalars(stmt)).all())
        skill_bindings_by_version_id = await self._skill_bindings_for_versions(
            workspace_service,
            [version.id for version in version_rows],
        )
        subagent_bindings_by_version_id = await self._subagent_bindings_for_versions(
            workspace_service,
            version_rows,
        )
        versions: dict[int, AgentPresetVersionResourceSpec] = {}
        for version in version_rows:
            versions[version.version] = AgentPresetVersionResourceSpec(
                version_number=version.version,
                instructions=version.instructions,
                tool_approvals=version.tool_approvals or {},
                actions=sorted(version.actions or []),
                skills=skill_bindings_by_version_id.get(version.id, []),
                subagents=subagent_bindings_by_version_id.get(version.id, []),
                catalog_id=version.catalog_id,
                model_name=version.model_name,
                model_provider=version.model_provider,
                base_url=version.base_url,
                output_type=version.output_type,
                namespaces=sorted(version.namespaces or []),
                retries=version.retries,
                enable_thinking=version.enable_thinking,
                enable_internet_access=version.enable_internet_access,
            )
        return versions

    async def _skill_bindings_for_versions(
        self,
        workspace_service: SyncMappingService,
        version_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[AgentPresetSkillBinding]]:
        """Return skill-head slug bindings grouped by preset version id."""
        if not version_ids:
            return {}
        stmt = (
            select(
                AgentPresetVersionSkill.preset_version_id,
                Skill.slug,
            )
            .select_from(AgentPresetVersionSkill)
            .join(Skill, AgentPresetVersionSkill.skill_id == Skill.id)
            .where(
                AgentPresetVersionSkill.workspace_id == workspace_service.workspace_id,
                AgentPresetVersionSkill.preset_version_id.in_(version_ids),
            )
            .order_by(
                AgentPresetVersionSkill.preset_version_id.asc(),
                Skill.slug.asc(),
            )
        )
        bindings: dict[uuid.UUID, list[AgentPresetSkillBinding]] = {}
        for preset_version_id, slug in (
            await workspace_service.session.execute(with_deleted(stmt))
        ).tuples():
            bindings.setdefault(preset_version_id, []).append(
                AgentPresetSkillBinding(slug=slug)
            )
        return bindings

    async def _subagent_bindings_for_versions(
        self,
        workspace_service: SyncMappingService,
        versions: list[AgentPresetVersion],
    ) -> dict[uuid.UUID, list[AgentPresetSubagentRef]]:
        """Return subagent bindings from the representation epoch of each row."""
        if not versions:
            return {}
        version_ids = [version.id for version in versions]
        stmt = (
            select(
                AgentPresetVersionSubagent.parent_preset_version_id,
                AgentPreset.slug,
                AgentPresetVersionSubagent.alias,
                AgentPresetVersionSubagent.description,
                AgentPresetVersionSubagent.max_turns,
            )
            .select_from(AgentPresetVersionSubagent)
            .join(
                AgentPreset,
                sa.and_(
                    AgentPreset.id == AgentPresetVersionSubagent.child_preset_id,
                    AgentPreset.workspace_id == AgentPresetVersionSubagent.workspace_id,
                ),
            )
            .where(
                AgentPresetVersionSubagent.workspace_id
                == workspace_service.workspace_id,
                AgentPresetVersionSubagent.parent_preset_version_id.in_(version_ids),
            )
            .order_by(
                AgentPresetVersionSubagent.parent_preset_version_id.asc(),
                AgentPresetVersionSubagent.alias.asc(),
            )
        )
        bindings: dict[uuid.UUID, list[AgentPresetSubagentRef]] = {}
        for preset_version_id, slug, alias, description, max_turns in (
            await workspace_service.session.execute(with_deleted(stmt))
        ).tuples():
            bindings.setdefault(preset_version_id, []).append(
                AgentPresetSubagentRef(
                    slug=slug,
                    version=None,
                    name=alias,
                    description=description,
                    max_turns=max_turns,
                )
            )
        for version in versions:
            if bindings.get(version.id):
                continue
            legacy_agents = _legacy_agents_config(version.agents)
            bindings[version.id] = [
                AgentPresetSubagentRef(
                    slug=ref.preset,
                    version=getattr(ref, "preset_version", None),
                    name=ref.name,
                    description=ref.description,
                    max_turns=ref.max_turns,
                )
                for ref in legacy_agents.subagents
            ]
        return bindings

    async def import_specs(
        self,
        workspace_service: SyncMappingService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile agent preset specs into the local database.

        Imports presets in two passes: the first upserts each preset, folder,
        and tag set; the second resolves subagent references and skill bindings
        in topological order and advances each preset's current head.
        """
        presets = workspace_spec.agent_presets
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
        # Pass 1 creates every logical ResourceHead before any version resolves
        # subagent edges.
        for source_id, spec in sorted(presets.items()):
            preset = await self._preset_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                swap=swap,
            )
            if preset is None:
                preset = AgentPreset(
                    workspace_id=workspace_service.workspace_id,
                    slug=spec.slug,
                    name=spec.name,
                    model_name=DEFAULT_AGENT_MODEL_NAME,
                    model_provider=DEFAULT_AGENT_MODEL_PROVIDER,
                )
            else:
                preset.slug = spec.slug
            preset.name = spec.name
            folder = await self._ensure_agent_folder(
                workspace_service, spec.folder_path
            )
            preset.folder_id = folder.id if folder is not None else None
            workspace_service.session.add(preset)
            # Flush so the preset has an id before tag links reference it.
            await workspace_service.session.flush()
            await self._replace_agent_tags(workspace_service, preset, spec.tags)
            preset_by_source_id[source_id] = preset

        # Pass 2 imports immutable versions after each child preset's current
        # pointer has been established.
        for source_id in import_order:
            spec = presets[source_id]
            preset = preset_by_source_id[source_id]
            current_import: (
                tuple[AgentPresetVersion, ResolvedAgentsConfig, list[Skill]] | None
            ) = None
            for version_number, version_spec in sorted(spec.versions.items()):
                agents, legacy_agents = await self._resolved_subagent_configs(
                    workspace_service,
                    version_spec,
                )
                skill_targets = await self._skill_binding_targets_for_spec(
                    workspace_service,
                    version_spec,
                )
                version = await self._upsert_agent_preset_version(
                    workspace_service,
                    preset=preset,
                    version=version_spec,
                    agents=agents,
                    legacy_agents=legacy_agents,
                    skill_targets=skill_targets,
                )
                if version_number == spec.current_version:
                    current_import = version, legacy_agents, skill_targets

            if spec.current_version is None:
                preset.current_version_id = None
            else:
                if current_import is None:
                    raise TracecatValidationError(
                        f"Agent preset {spec.slug!r} current version "
                        f"{spec.current_version} is missing from the version snapshots."
                    )
                current_version, legacy_agents, skill_targets = current_import
                preset.current_version_id = current_version.id
                await self._sync_legacy_preset_head(
                    workspace_service,
                    preset=preset,
                    version=current_version,
                    agents=legacy_agents,
                    skill_targets=skill_targets,
                )
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
            subagents: list[AgentPresetSubagentRef] = []
            for version in spec.versions.values():
                subagents.extend(version.subagents)
            for subagent in sorted(subagents, key=lambda item: item.slug):
                # Ignore refs to slugs not present in this sync batch.
                if child_source_id := slug_to_source_id.get(subagent.slug):
                    visit(child_source_id)
            visiting.remove(source_id)
            visited.add(source_id)
            ordered.append(source_id)

        for source_id in sorted(presets):
            visit(source_id)
        return ordered

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
            await self._row_by_source_id(
                workspace_service,
                source_id=source_id,
                model=AgentPreset,
                options=(selectinload(AgentPreset.tags),),
                row_predicates=(AgentPreset.deleted_at.is_(None),),
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

    async def _resolved_subagent_configs(
        self,
        workspace_service: SyncMappingService,
        spec: AgentPresetVersionResourceSpec,
    ) -> tuple[AgentSubagentsConfig, ResolvedAgentsConfig]:
        """Resolve normalized and rollback subagent configs in one pass.

        Resolves each subagent reference to its ResourceHead, skipping any that
        are missing or unpublished. Version selectors in older manifests are
        compatibility-only input and do not create version-to-version edges.
        """
        head_refs: list[AnyAttachedSubagentRef] = []
        resolved_refs: list[ResolvedAttachedSubagentRef] = []
        for subagent in spec.subagents:
            result = await workspace_service.session.execute(
                select(AgentPreset, AgentPresetVersion)
                .join(
                    AgentPresetVersion,
                    sa.and_(
                        AgentPresetVersion.workspace_id == AgentPreset.workspace_id,
                        AgentPresetVersion.id == AgentPreset.current_version_id,
                        AgentPresetVersion.preset_id == AgentPreset.id,
                    ),
                )
                .where(
                    AgentPreset.workspace_id == workspace_service.workspace_id,
                    AgentPreset.slug == subagent.slug,
                    AgentPreset.deleted_at.is_(None),
                )
            )
            row = result.tuples().one_or_none()
            if row is None:
                continue
            child, child_version = row
            ref_fields = {
                "preset": child.slug,
                "preset_id": child.id,
                "name": subagent.name,
                "description": subagent.description,
                "max_turns": subagent.max_turns,
            }
            head_refs.append(HeadAttachedSubagentRef(**ref_fields))
            resolved_refs.append(
                ResolvedAttachedSubagentRef(
                    **ref_fields,
                    preset_version_id=child_version.id,
                    preset_version=child_version.version,
                )
            )
        enabled = bool(head_refs)
        return (
            AgentSubagentsConfig(enabled=enabled, subagents=head_refs),
            ResolvedAgentsConfig(enabled=enabled, subagents=resolved_refs),
        )

    async def _version_matches_import(
        self,
        workspace_service: SyncMappingService,
        version: AgentPresetVersion,
        attrs: Mapping[str, Any],
        skill_targets: list[Skill],
        agents: AgentSubagentsConfig,
    ) -> bool:
        """Return whether an immutable version exactly matches an import."""
        for key, value in attrs.items():
            # MCP integrations are workspace-local and intentionally excluded
            # from the portable immutable resource representation.
            if key == "mcp_integrations":
                continue
            existing_value = getattr(version, key)
            if key in {"actions", "namespaces"}:
                existing_value = sorted(existing_value) if existing_value else None
            elif key == "tool_approvals" and not existing_value:
                existing_value = None
            if existing_value != value:
                return False
        desired_skill_targets = {skill.id for skill in skill_targets}
        existing_skill_targets = set(
            (
                await workspace_service.session.scalars(
                    select(AgentPresetVersionSkill.skill_id).where(
                        AgentPresetVersionSkill.workspace_id
                        == workspace_service.workspace_id,
                        AgentPresetVersionSkill.preset_version_id == version.id,
                    )
                )
            ).all()
        )
        if existing_skill_targets != desired_skill_targets:
            return False
        desired_subagents = {
            (
                subagent.preset_id,
                subagent.alias,
                subagent.description,
                subagent.max_turns,
            )
            for subagent in agents.subagents
            if isinstance(subagent, HeadAttachedSubagentRef)
        }
        existing_subagents = set(
            (
                await workspace_service.session.execute(
                    select(
                        AgentPresetVersionSubagent.child_preset_id,
                        AgentPresetVersionSubagent.alias,
                        AgentPresetVersionSubagent.description,
                        AgentPresetVersionSubagent.max_turns,
                    ).where(
                        AgentPresetVersionSubagent.workspace_id
                        == workspace_service.workspace_id,
                        AgentPresetVersionSubagent.parent_preset_version_id
                        == version.id,
                    )
                )
            ).tuples()
        )
        if not existing_subagents:
            legacy_agents = _legacy_agents_config(version.agents)
            if legacy_agents.enabled != agents.enabled:
                return False
            desired_by_slug = {
                (
                    subagent.preset,
                    subagent.alias,
                    subagent.description,
                    subagent.max_turns,
                )
                for subagent in agents.subagents
                if isinstance(subagent, HeadAttachedSubagentRef)
            }
            legacy_by_slug = {
                (ref.preset, ref.alias, ref.description, ref.max_turns)
                for ref in legacy_agents.subagents
            }
            return legacy_by_slug == desired_by_slug
        return existing_subagents == desired_subagents

    async def _upsert_agent_preset_version(
        self,
        workspace_service: SyncMappingService,
        *,
        preset: AgentPreset,
        version: AgentPresetVersionResourceSpec,
        agents: AgentSubagentsConfig,
        legacy_agents: ResolvedAgentsConfig,
        skill_targets: list[Skill],
    ) -> AgentPresetVersion:
        """Create an exact-number version or verify an identical existing one."""
        existing = await workspace_service.session.scalar(
            select(AgentPresetVersion).where(
                AgentPresetVersion.workspace_id == workspace_service.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
                AgentPresetVersion.version == version.version_number,
            )
        )
        attrs = self._version_attrs_from_spec(version)
        if existing is None:
            # MCP selections are workspace-local rather than portable Git state.
            # Carry the destination preset's local selection into a newly imported
            # version so advancing the imported head does not silently clear it.
            attrs["mcp_integrations"] = preset.mcp_integrations
            existing = AgentPresetVersion(
                workspace_id=workspace_service.workspace_id,
                preset_id=preset.id,
                version=version.version_number,
                agents=legacy_agents.model_dump(mode="json"),
                **attrs,
            )
        else:
            if not await self._version_matches_import(
                workspace_service,
                existing,
                attrs,
                skill_targets,
                agents,
            ):
                raise TracecatValidationError(
                    f"Agent preset {preset.slug!r} version "
                    f"{version.version_number} already exists with different content",
                    detail={
                        "code": "immutable_preset_version_conflict",
                        "preset_slug": preset.slug,
                        "version": version.version_number,
                    },
                )
            await self._sync_legacy_version_projection(
                workspace_service,
                version=existing,
                agents=agents,
                legacy_agents=legacy_agents,
                skill_targets=skill_targets,
            )
            return existing
        workspace_service.session.add(existing)
        await workspace_service.session.flush()
        for skill in skill_targets:
            workspace_service.session.add(
                AgentPresetVersionSkill(
                    workspace_id=workspace_service.workspace_id,
                    preset_version_id=existing.id,
                    skill_id=skill.id,
                    skill_version_id=skill.current_version_id,
                )
            )
        await workspace_service.session.flush()
        await self._add_version_subagent_bindings(
            workspace_service,
            existing,
            agents,
        )
        return existing

    def _version_attrs_from_spec(
        self,
        spec: AgentPresetVersionResourceSpec,
    ) -> dict[str, Any]:
        """Return version row attributes from a version spec."""
        return {
            "instructions": spec.instructions,
            "model_name": spec.model_name or DEFAULT_AGENT_MODEL_NAME,
            "model_provider": spec.model_provider or DEFAULT_AGENT_MODEL_PROVIDER,
            "catalog_id": spec.catalog_id,
            "base_url": spec.base_url,
            "output_type": spec.output_type,
            "actions": sorted(spec.actions) or None,
            "namespaces": sorted(spec.namespaces) or None,
            "tool_approvals": _tool_approvals(spec.tool_approvals),
            "mcp_integrations": None,
            "retries": spec.retries,
            "enable_thinking": spec.enable_thinking,
            "enable_internet_access": spec.enable_internet_access,
        }

    async def _sync_legacy_version_projection(
        self,
        workspace_service: SyncMappingService,
        *,
        version: AgentPresetVersion,
        agents: AgentSubagentsConfig,
        legacy_agents: ResolvedAgentsConfig,
        skill_targets: list[Skill],
    ) -> None:
        """Heal rollback-only columns on an existing immutable version."""

        version.agents = legacy_agents.model_dump(mode="json")
        workspace_service.session.add(version)
        await workspace_service.session.execute(
            sa.delete(AgentPresetVersionSubagent).where(
                AgentPresetVersionSubagent.workspace_id
                == workspace_service.workspace_id,
                AgentPresetVersionSubagent.parent_preset_version_id == version.id,
            )
        )
        await self._add_version_subagent_bindings(
            workspace_service,
            version,
            agents,
        )
        for skill in skill_targets:
            if skill.current_version_id is None:
                continue
            await workspace_service.session.execute(
                sa.update(AgentPresetVersionSkill)
                .where(
                    AgentPresetVersionSkill.workspace_id
                    == workspace_service.workspace_id,
                    AgentPresetVersionSkill.preset_version_id == version.id,
                    AgentPresetVersionSkill.skill_id == skill.id,
                )
                .values(skill_version_id=skill.current_version_id)
            )

    async def _sync_legacy_preset_head(
        self,
        workspace_service: SyncMappingService,
        *,
        preset: AgentPreset,
        version: AgentPresetVersion,
        agents: ResolvedAgentsConfig,
        skill_targets: list[Skill],
    ) -> None:
        """Dual-write the mutable preset projection consumed by the old app."""

        for field in AGENT_PRESET_EXECUTION_FIELDS:
            if field != "agents":
                setattr(preset, field, getattr(version, field))
        preset.agents = agents.model_dump(mode="json")
        await workspace_service.session.execute(
            sa.delete(AgentPresetSkill).where(
                AgentPresetSkill.workspace_id == workspace_service.workspace_id,
                AgentPresetSkill.preset_id == preset.id,
            )
        )
        for skill in skill_targets:
            if skill.current_version_id is None:
                continue
            workspace_service.session.add(
                AgentPresetSkill(
                    workspace_id=workspace_service.workspace_id,
                    preset_id=preset.id,
                    skill_id=skill.id,
                    skill_version_id=skill.current_version_id,
                )
            )

    async def _add_version_subagent_bindings(
        self,
        workspace_service: SyncMappingService,
        version: AgentPresetVersion,
        agents: AgentSubagentsConfig,
    ) -> None:
        """Add subagent ResourceHead edges to a newly created version."""
        for subagent in agents.subagents:
            if not isinstance(subagent, HeadAttachedSubagentRef):
                continue
            workspace_service.session.add(
                AgentPresetVersionSubagent(
                    workspace_id=workspace_service.workspace_id,
                    parent_preset_version_id=version.id,
                    child_preset_id=subagent.preset_id,
                    alias=subagent.alias,
                    description=subagent.description,
                    max_turns=subagent.max_turns,
                )
            )
        await workspace_service.session.flush()

    async def _skill_binding_targets_for_spec(
        self,
        workspace_service: SyncMappingService,
        spec: AgentPresetVersionResourceSpec,
    ) -> list[Skill]:
        """Resolve ``spec``'s skill bindings to published Skill ResourceHeads.

        Skips bindings whose live skill or current published version is missing.
        """
        targets: list[Skill] = []
        skill_service = SkillService(
            session=workspace_service.session,
            role=workspace_service.role,
        )
        for binding in spec.skills:
            skill = await skill_service.get_skill_by_slug(binding.slug)
            if skill is None:
                continue
            if skill.current_version_id is None:
                raise TracecatValidationError(
                    f"Skill {binding.slug!r} has no published version",
                    detail={
                        "code": "skill_not_published",
                        "skill_slug": binding.slug,
                    },
                )
            targets.append(skill)
        return targets


def _parse_preset_version_relpath(relpath: str) -> int | None:
    """Parse ``versions/<n>.yml`` relpaths for preset companion files."""
    parts = path_parts(relpath)
    if len(parts) != 2 or parts[0] != AGENT_PRESET_VERSIONS_DIR:
        return None
    filename = parts[1]
    if not filename.endswith(".yml"):
        return None
    try:
        version_number = int(filename.removesuffix(".yml"))
    except ValueError:
        return None
    return version_number if version_number >= 1 else None


def _parse_agent_preset_version_manifest(
    adapter: AgentPresetAdapter,
    *,
    source_id: str,
    version_number: int,
    content: str,
    diagnostics: list[PullDiagnostic],
) -> AgentPresetVersionResourceSpec | None:
    """Parse one agent preset version manifest or append a diagnostic."""
    path = adapter._version_source_path(source_id, version_number)
    try:
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict) or not raw:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=None,
                    error_type="parse",
                    message="Empty or invalid agent preset version YAML file",
                    details={},
                )
            )
            return None
        version = AgentPresetVersionResourceSpec.model_validate(raw)
        if version.version_number != version_number:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=version.name,
                    error_type="validation",
                    message=(
                        "Agent preset version number does not match its repository path"
                    ),
                    details={
                        "path_version": version_number,
                        "spec_version": version.version_number,
                    },
                )
            )
            return None
        return version
    except yaml.YAMLError as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=None,
                error_type="parse",
                message=f"YAML parsing error: {str(e)}",
                details={"yaml_error": str(e)},
            )
        )
    except ValidationError as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=None,
                error_type="validation",
                message=f"Validation error: {str(e)}",
                details={
                    "validation_errors": serializable_validation_errors(e.errors())
                },
            )
        )
    return None


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
