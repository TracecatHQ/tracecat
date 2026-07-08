"""Skill resource adapter (skill manifest plus versioned file blobs)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from collections import defaultdict
from collections.abc import Mapping
from typing import Literal, cast

import orjson
import sqlalchemy as sa
import yaml
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.skill.service import SkillFileBlobRef, SkillService
from tracecat.db.models import (
    AgentPreset,
    AgentPresetSkill,
    AgentPresetVersionSkill,
    Skill,
    SkillBlob,
    SkillVersion,
    SkillVersionFile,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.storage import blob
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
    SKILL_ROOT,
    SkillFileSpec,
    SkillResourceSpec,
    SkillVersionResourceSpec,
    WorkspaceManifestResources,
    WorkspaceSpec,
)
from tracecat.workspace_sync.serialization import serialize_yaml_model

SKILL_FILENAME = "skill.yml"
SKILL_FILES_DIR = "files"
SKILL_VERSIONS_DIR = "versions"
SKILL_VERSION_FILENAME = "version.yml"


class SkillAdapter(DirectoryManifestAdapter):
    """Adapter for skills: a manifest file plus versioned file blob snapshots."""

    resource_type = SyncResourceType.SKILL
    spec_attr = "skills"
    model = SkillResourceSpec
    read_scope = "agent:read"
    create_scope = "agent:create"
    update_scope = "agent:update"
    root = SKILL_ROOT
    filename = SKILL_FILENAME
    import_identity_attrs = ("slug",)
    import_identity_noun = "slug"

    def _version_manifest_path(self, source_id: str, version: int) -> str:
        """Return the repository path for a skill version manifest."""
        return (
            f"{self.root}/{source_id}/{SKILL_VERSIONS_DIR}/"
            f"{version}/{SKILL_VERSION_FILENAME}"
        )

    def _version_file_source_path(
        self,
        source_id: str,
        version: int,
        file_path: str,
    ) -> str:
        """Return the repository path for a versioned skill file blob."""
        return (
            f"{self.root}/{source_id}/{SKILL_VERSIONS_DIR}/"
            f"{version}/{SKILL_FILES_DIR}/{file_path}"
        )

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
        """Map a skill companion path to ``(source_id, relpath)``.

        Versioned snapshots live under ``versions/<n>/``. The returned relpath
        is interpreted by :meth:`attach_extra_files`.
        """
        parts = path_parts(path)
        root_parts = path_parts(roots.skills)
        # The leading segments must match the configured skills root exactly.
        if parts[: len(root_parts)] != root_parts:
            return None
        # A companion file needs at least root + <source_id> + one nested
        # segment; the primary "skill.yml" lives directly below the source id.
        if len(parts) < len(root_parts) + 3:
            return None
        source_id = parts[len(root_parts)]
        if not source_id:
            return None
        relpath = "/".join(parts[len(root_parts) + 1 :])
        return (source_id, relpath) if relpath else None

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize a skill's versioned file blobs to their repository paths."""
        skill = cast(SkillResourceSpec, spec)
        files: dict[str, str] = {}
        for version_number, version in sorted(skill.versions.items()):
            files[self._version_manifest_path(source_id, version_number)] = (
                serialize_yaml_model(version)
            )
            files.update(
                {
                    self._version_file_source_path(
                        source_id,
                        version_number,
                        file_path,
                    ): content
                    for file_path, content in sorted(version.file_contents.items())
                }
            )
        return files

    def attach_extra_files(
        self,
        specs: dict[str, BaseModel],
        extra_files: Mapping[tuple[str, str], str],
        diagnostics: list[PullDiagnostic],
    ) -> dict[str, BaseModel]:
        """Fold parsed skill file blobs back into each skill spec.

        Attaches version file contents by source id and emits a
        :class:`PullDiagnostic` for any declared version file that is missing or
        whose SHA256 does not match the manifest.
        """
        # Group the flat (source_id, relpath) blob map by skill so each version
        # spec can look up only its own files below.
        version_manifest_by_source: dict[str, dict[int, str]] = defaultdict(dict)
        version_contents_by_source: dict[str, dict[int, dict[str, str]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for (source_id, relpath), content in extra_files.items():
            parsed = _parse_skill_version_relpath(relpath)
            if parsed is None:
                continue
            version_number, kind, file_path = parsed
            if kind == "manifest":
                version_manifest_by_source[source_id][version_number] = content
            elif file_path:
                version_contents_by_source[source_id][version_number][file_path] = (
                    content
                )

        updated: dict[str, BaseModel] = {}
        for source_id, base_spec in specs.items():
            spec = cast(SkillResourceSpec, base_spec)
            versions: dict[int, SkillVersionResourceSpec] = dict(spec.versions)
            for version_number, content in sorted(
                version_manifest_by_source.get(source_id, {}).items()
            ):
                version_spec = _parse_skill_version_manifest(
                    self,
                    source_id=source_id,
                    version_number=version_number,
                    content=content,
                    diagnostics=diagnostics,
                )
                if version_spec is None:
                    continue
                version_contents = version_contents_by_source[source_id].get(
                    version_number, {}
                )
                self._validate_version_files(
                    source_id=source_id,
                    spec=spec,
                    version=version_spec,
                    contents=version_contents,
                    diagnostics=diagnostics,
                )
                versions[version_number] = version_spec.model_copy(
                    update={"file_contents": version_contents}
                )

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
                            f"Skill {spec.slug!r} current version "
                            f"{spec.current_version} is missing"
                        ),
                        details={
                            "skill_slug": spec.slug,
                            "skill_version": spec.current_version,
                        },
                    )
                )

            # Attach the resolved version contents even when diagnostics fired
            # so the caller has whatever did parse; diagnostics gate acceptance.
            updated[source_id] = spec.model_copy(
                update={"files": [], "file_contents": {}, "versions": versions}
            )
        return updated

    def _validate_version_files(
        self,
        *,
        source_id: str,
        spec: SkillResourceSpec,
        version: SkillVersionResourceSpec,
        contents: Mapping[str, str],
        diagnostics: list[PullDiagnostic],
    ) -> None:
        """Validate a parsed version's declared file hashes."""
        for file_spec in version.files:
            content = contents.get(file_spec.path)
            if content is None:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=self._version_manifest_path(
                            source_id,
                            version.version_number,
                        ),
                        workflow_title=version.name,
                        error_type="dependency",
                        message=(
                            f"Skill version {spec.slug!r}@{version.version_number} "
                            f"file {file_spec.path!r} is missing"
                        ),
                        details={
                            "skill_slug": spec.slug,
                            "skill_version": version.version_number,
                            "file_path": file_spec.path,
                        },
                    )
                )
                continue
            try:
                content_bytes = _skill_file_content_bytes(file_spec, content)
            except ValueError as e:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=self._version_file_source_path(
                            source_id,
                            version.version_number,
                            file_spec.path,
                        ),
                        workflow_title=version.name,
                        error_type="validation",
                        message=(
                            f"Skill version {spec.slug!r}@{version.version_number} "
                            f"file {file_spec.path!r} could not be decoded: {e}"
                        ),
                        details={
                            "skill_slug": spec.slug,
                            "skill_version": version.version_number,
                            "file_path": file_spec.path,
                            "encoding": file_spec.encoding,
                        },
                    )
                )
                continue
            actual_hash = hashlib.sha256(content_bytes).hexdigest()
            if actual_hash != file_spec.sha256:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=self._version_file_source_path(
                            source_id,
                            version.version_number,
                            file_spec.path,
                        ),
                        workflow_title=version.name,
                        error_type="validation",
                        message=(
                            f"Skill version {spec.slug!r}@{version.version_number} "
                            f"file {file_spec.path!r} SHA256 does not match"
                        ),
                        details={
                            "skill_slug": spec.slug,
                            "skill_version": version.version_number,
                            "file_path": file_spec.path,
                            "expected_sha256": file_spec.sha256,
                            "actual_sha256": actual_hash,
                        },
                    )
                )

    async def project(
        self, workspace_service: SyncMappingService
    ) -> ResourceProjection:
        """Project skills and the version snapshots needed to preserve pins."""
        stmt = self._projection_stmt(workspace_service)
        skills = list((await workspace_service.session.execute(stmt)).scalars().all())
        versions_by_skill_id = await self._exported_bound_versions_by_skill(
            workspace_service
        )
        return await self._projection_from_skills(
            workspace_service,
            skills,
            versions_by_skill_id=versions_by_skill_id,
        )

    async def project_dependency_refs(
        self,
        workspace_service: SyncMappingService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project skills selected directly or referenced by slug."""
        if refs.select_all:
            return await self.project(workspace_service)
        versioned_slugs = {slug for slug, _version in refs.versioned_slugs}
        slugs = set(refs.slugs) | versioned_slugs
        if not refs.local_ids and not refs.source_ids and not slugs:
            return ResourceProjection(specs={}, resources=[])

        local_ids = set(refs.local_ids)
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
        if local_ids and slugs:
            stmt = stmt.where(sa.or_(Skill.id.in_(local_ids), Skill.name.in_(slugs)))
        elif local_ids:
            stmt = stmt.where(Skill.id.in_(local_ids))
        else:
            stmt = stmt.where(Skill.name.in_(slugs))
        skills = list((await workspace_service.session.execute(stmt)).scalars().all())
        versions_by_slug: dict[str, set[int]] = defaultdict(set)
        for slug, version in refs.versioned_slugs:
            versions_by_slug[slug].add(version)
        versions_by_skill_id = {
            skill.id: versions_by_slug[skill.name]
            for skill in skills
            if skill.name in versions_by_slug
        }
        return await self._projection_from_skills(
            workspace_service,
            skills,
            versions_by_skill_id=versions_by_skill_id,
        )

    def _projection_stmt(
        self, workspace_service: SyncMappingService
    ) -> sa.Select[tuple[Skill]]:
        """Build the base eager-loaded skill projection query."""
        return (
            select(Skill)
            .where(
                Skill.workspace_id == workspace_service.workspace_id,
                Skill.archived_at.is_(None),
            )
            .options(selectinload(Skill.current_version))
            .order_by(Skill.name.asc(), Skill.id.asc())
        )

    async def _projection_from_skills(
        self,
        workspace_service: SyncMappingService,
        skills: list[Skill],
        versions_by_skill_id: Mapping[uuid.UUID, set[int]] | None = None,
    ) -> ResourceProjection:
        """Build sync specs from eager-loaded skill rows."""
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for skill in skills:
            source_id = assigner.assign(skill.id, skill.name)
            # Keep the top-level manifest as metadata only, then emit immutable
            # file snapshots below ``versions/`` for current and pinned versions.
            version = skill.current_version
            version_numbers = set((versions_by_skill_id or {}).get(skill.id, set()))
            if version is not None:
                version_numbers.add(version.version)
            versions = await self._version_specs_for_skill(
                workspace_service,
                skill=skill,
                version_numbers=version_numbers,
            )

            specs[source_id] = SkillResourceSpec(
                id=source_id,
                slug=skill.name,
                name=version.name if version is not None else skill.name,
                current_version=version.version if version is not None else None,
                description=skill.description,
                versions=versions,
            )
            resources.append(self.projected_resource(source_id, skill.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def _exported_bound_versions_by_skill(
        self,
        workspace_service: SyncMappingService,
    ) -> dict[uuid.UUID, set[int]]:
        """Return skill versions bound by exported agent preset state."""
        # Soft-deleted presets keep their skill bindings but are excluded from the
        # preset projection, so their pins must not keep versions exported.
        head_stmt = (
            select(SkillVersion.skill_id, SkillVersion.version)
            .join(
                AgentPresetSkill,
                AgentPresetSkill.skill_version_id == SkillVersion.id,
            )
            .join(
                AgentPreset,
                AgentPresetSkill.preset_id == AgentPreset.id,
            )
            .where(
                AgentPresetSkill.workspace_id == workspace_service.workspace_id,
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.deleted_at.is_(None),
            )
        )
        current_version_stmt = (
            select(SkillVersion.skill_id, SkillVersion.version)
            .select_from(AgentPresetVersionSkill)
            .join(
                AgentPreset,
                AgentPreset.current_version_id
                == AgentPresetVersionSkill.preset_version_id,
            )
            .join(
                SkillVersion,
                AgentPresetVersionSkill.skill_version_id == SkillVersion.id,
            )
            .where(
                AgentPresetVersionSkill.workspace_id == workspace_service.workspace_id,
                AgentPreset.workspace_id == workspace_service.workspace_id,
                AgentPreset.deleted_at.is_(None),
            )
        )
        versions_by_skill_id: dict[uuid.UUID, set[int]] = defaultdict(set)
        for stmt in (head_stmt, current_version_stmt):
            for skill_id, version_number in (
                await workspace_service.session.execute(stmt)
            ).tuples():
                versions_by_skill_id[skill_id].add(version_number)
        return versions_by_skill_id

    async def _version_specs_for_skill(
        self,
        workspace_service: SyncMappingService,
        *,
        skill: Skill,
        version_numbers: set[int],
    ) -> dict[int, SkillVersionResourceSpec]:
        """Build version specs for the requested version numbers of ``skill``."""
        if not version_numbers:
            return {}
        stmt = (
            select(SkillVersion)
            .where(
                SkillVersion.workspace_id == workspace_service.workspace_id,
                SkillVersion.skill_id == skill.id,
                SkillVersion.version.in_(version_numbers),
            )
            .order_by(SkillVersion.version.asc())
        )
        versions: dict[int, SkillVersionResourceSpec] = {}
        for version in (await workspace_service.session.scalars(stmt)).all():
            files: list[SkillFileSpec] = []
            file_contents: dict[str, str] = {}
            rows = await self._skill_version_rows(workspace_service, version.id)
            for version_file, blob_row in rows:
                content = await blob.download_file(
                    key=blob_row.key,
                    bucket=blob_row.bucket,
                )
                content_text, encoding = _skill_file_content_for_git(content)
                files.append(
                    SkillFileSpec(
                        path=version_file.path,
                        sha256=blob_row.sha256,
                        encoding=encoding,
                    )
                )
                file_contents[version_file.path] = content_text
            versions[version.version] = SkillVersionResourceSpec(
                version_number=version.version,
                name=version.name,
                description=version.description,
                files=files,
                file_contents=file_contents,
            )
        return versions

    async def _skill_version_rows(
        self,
        workspace_service: SyncMappingService,
        version_id: uuid.UUID,
    ) -> list[tuple[SkillVersionFile, SkillBlob]]:
        """Return a version's files joined to their blobs, ordered by path."""
        # Join each version file to its blob row so callers get content
        # location and digest together; order by path for stable output.
        stmt = (
            select(SkillVersionFile, SkillBlob)
            .join(SkillBlob, SkillVersionFile.blob_id == SkillBlob.id)
            .where(
                SkillVersionFile.workspace_id == workspace_service.workspace_id,
                SkillVersionFile.skill_version_id == version_id,
            )
            .order_by(SkillVersionFile.path.asc())
        )
        return [
            (version_file, blob_row)
            for version_file, blob_row in (
                await workspace_service.session.execute(stmt)
            ).all()
        ]

    async def import_specs(
        self,
        workspace_service: SyncMappingService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile skill specs into the local database.

        Upserts each skill, stores its file contents as deduplicated blobs, and
        creates or updates the target :class:`SkillVersion` rows (rewriting
        their file rows and recomputing manifest hashes) before pinning the
        declared current version.
        """
        skills = workspace_spec.skills
        # Skill identity lives on ``Skill.name`` but specs key off ``slug``; the
        # shorter temp prefix keeps placeholders within the slug length budget.
        swap = await self.plan_name_swap(
            workspace_service,
            targets={source_id: spec.slug for source_id, spec in skills.items()},
            model=Skill,
            name_column=Skill.name,
            noun="slug",
            kind_label="Skill",
            owner_label="skill",
            error_cls=TracecatValidationError,
            temp_prefix="__tc_sync_tmp_",
        )
        imported: list[ImportedResource] = []
        skill_service = SkillService(
            session=workspace_service.session, role=workspace_service.role
        )
        # Sort by source id so imports apply in a deterministic order.
        for source_id, spec in sorted(skills.items()):
            # Stage 1: locate or create the skill row this spec maps to.
            skill = await self._skill_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                swap=swap,
            )
            if skill is None:
                skill = Skill(
                    workspace_id=workspace_service.workspace_id,
                    name=spec.slug,
                    description=getattr(spec, "description", None),
                    draft_revision=0,
                )
                workspace_service.session.add(skill)
                # Flush to assign skill.id before referencing it below.
                await workspace_service.session.flush()
            else:
                # Keep an existing skill's slug and description in sync.
                skill.name = spec.slug
                skill.description = getattr(spec, "description", None)

            version_specs = dict(spec.versions)
            if (
                spec.current_version is not None
                and spec.current_version not in version_specs
            ):
                raise TracecatValidationError(
                    f"Skill {spec.slug!r} current version {spec.current_version} "
                    "is missing from the version snapshots."
                )

            imported_versions: dict[int, SkillVersion] = {}
            imported_version_file_refs: dict[int, dict[str, SkillFileBlobRef]] = {}
            for version_number, version_spec in sorted(version_specs.items()):
                imported_version, file_refs = await self._upsert_skill_version(
                    workspace_service,
                    skill_service,
                    skill=skill,
                    version=version_spec,
                )
                imported_versions[version_number] = imported_version
                imported_version_file_refs[version_number] = file_refs

            if spec.current_version is not None:
                if current := imported_versions.get(spec.current_version):
                    skill.current_version_id = current.id
                    await skill_service._replace_draft_with_blob_map(
                        skill=skill,
                        path_to_blob=imported_version_file_refs[spec.current_version],
                    )
            else:
                # Git says the skill is unversioned: drop any stale head pin so an
                # existing skill stops pointing at a version it no longer declares,
                # and clear draft files copied from the previously pinned version.
                skill.current_version_id = None
                await skill_service._replace_draft_with_blob_map(
                    skill=skill,
                    path_to_blob={},
                )
            workspace_service.session.add(skill)
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, skill.id))
        return imported

    async def _upsert_skill_version(
        self,
        workspace_service: SyncMappingService,
        skill_service: SkillService,
        *,
        skill: Skill,
        version: SkillVersionResourceSpec,
    ) -> tuple[SkillVersion, dict[str, SkillFileBlobRef]]:
        """Create or update one skill version and its file rows."""
        file_refs: list[tuple[str, SkillFileBlobRef]] = []
        for file_spec in version.files:
            content_text = version.file_contents.get(file_spec.path)
            if content_text is None:
                raise TracecatValidationError(
                    f"Skill version {version.name!r}@{version.version_number} "
                    f"declares file {file_spec.path!r} but no content was provided."
                )
            try:
                content = _skill_file_content_bytes(file_spec, content_text)
            except ValueError as e:
                raise TracecatValidationError(
                    f"Skill version {version.name!r}@{version.version_number} "
                    f"file {file_spec.path!r} could not be decoded: {e}"
                ) from e
            blob_row = await skill_service._get_or_create_blob(content=content)
            file_refs.append(
                (
                    file_spec.path,
                    SkillFileBlobRef(
                        blob=blob_row,
                        content_type=skill_service._guess_content_type(file_spec.path),
                    ),
                )
            )

        existing = await workspace_service.session.scalar(
            select(SkillVersion).where(
                SkillVersion.workspace_id == workspace_service.workspace_id,
                SkillVersion.skill_id == skill.id,
                SkillVersion.version == version.version_number,
            )
        )
        manifest_payload = [
            {
                "path": path,
                "sha256": file_ref.blob.sha256,
                "size_bytes": file_ref.blob.size_bytes,
                "content_type": file_ref.content_type,
            }
            for path, file_ref in sorted(file_refs, key=lambda item: item[0])
        ]
        manifest_sha256 = skill_service._compute_sha256(orjson.dumps(manifest_payload))
        attrs = {
            "manifest_sha256": manifest_sha256,
            "file_count": len(file_refs),
            "total_size_bytes": sum(
                file_ref.blob.size_bytes for _, file_ref in file_refs
            ),
            "name": version.name,
            "description": version.description,
        }
        if existing is None:
            existing = SkillVersion(
                workspace_id=workspace_service.workspace_id,
                skill_id=skill.id,
                version=version.version_number,
                **attrs,
            )
            workspace_service.session.add(existing)
            await workspace_service.session.flush()
        else:
            for key, value in attrs.items():
                setattr(existing, key, value)
            await workspace_service.session.execute(
                sa.delete(SkillVersionFile).where(
                    SkillVersionFile.workspace_id == workspace_service.workspace_id,
                    SkillVersionFile.skill_version_id == existing.id,
                )
            )

        for path, file_ref in sorted(file_refs, key=lambda item: item[0]):
            workspace_service.session.add(
                SkillVersionFile(
                    workspace_id=workspace_service.workspace_id,
                    skill_version_id=existing.id,
                    path=path,
                    blob_id=file_ref.blob.id,
                    content_type=file_ref.content_type,
                )
            )
        await workspace_service.session.flush()
        return existing, dict(file_refs)

    async def _skill_for_import(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
        spec: SkillResourceSpec,
        swap: NameSwapPlan[Skill],
    ) -> Skill | None:
        """Resolve the existing skill to update for ``source_id``, if any.

        Prefers the skill already mapped to ``source_id`` (validating the slug
        is still free), then falls back to matching on slug. Returns ``None``
        when a new skill must be created.
        """
        # Prefer the skill already mapped to this source id, but only after
        # confirming its incoming slug does not clash with another skill.
        skill = swap.mapped_by_source_id.get(source_id) or (
            await self._skill_by_source_id(
                workspace_service,
                source_id=source_id,
            )
        )
        if skill is not None:
            await swap.ensure_available(
                workspace_service,
                source_id=source_id,
                name=spec.slug,
                row_id=skill.id,
            )
            return skill

        # No mapping yet: fall back to matching an existing skill by slug.
        return await workspace_service.session.scalar(
            select(Skill).where(
                Skill.workspace_id == workspace_service.workspace_id,
                Skill.name == spec.slug,
            )
        )

    async def _skill_by_source_id(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
    ) -> Skill | None:
        """Load the skill mapped to ``source_id`` via the sync mapping, if any."""
        return await self._row_by_source_id(
            workspace_service, source_id=source_id, model=Skill
        )


def _parse_skill_version_relpath(relpath: str) -> tuple[int, str, str | None] | None:
    """Parse ``versions/<n>/...`` relpaths for skill companion files."""
    parts = path_parts(relpath)
    if len(parts) < 3 or parts[0] != SKILL_VERSIONS_DIR:
        return None
    try:
        version_number = int(parts[1])
    except ValueError:
        return None
    if version_number < 1:
        return None
    if len(parts) == 3 and parts[2] == SKILL_VERSION_FILENAME:
        return version_number, "manifest", None
    if len(parts) >= 4 and parts[2] == SKILL_FILES_DIR:
        file_path = "/".join(parts[3:])
        return (version_number, "file", file_path) if file_path else None
    return None


def _parse_skill_version_manifest(
    adapter: SkillAdapter,
    *,
    source_id: str,
    version_number: int,
    content: str,
    diagnostics: list[PullDiagnostic],
) -> SkillVersionResourceSpec | None:
    """Parse one skill version manifest or append a diagnostic."""
    path = adapter._version_manifest_path(source_id, version_number)
    try:
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict) or not raw:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=None,
                    error_type="parse",
                    message="Empty or invalid skill version YAML file",
                    details={},
                )
            )
            return None
        version = SkillVersionResourceSpec.model_validate(raw)
        if version.version_number != version_number:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=version.name,
                    error_type="validation",
                    message="Skill version number does not match its repository path",
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


def _skill_file_content_for_git(content: bytes) -> tuple[str, Literal["base64"] | None]:
    """Return Git-safe text plus an encoding marker for a skill file blob."""
    try:
        return content.decode("utf-8"), None
    except UnicodeDecodeError:
        return base64.b64encode(content).decode("ascii"), "base64"


def _skill_file_content_bytes(file_spec: SkillFileSpec, content: str) -> bytes:
    """Return original skill file bytes from repository text content."""
    if file_spec.encoding == "base64":
        try:
            normalized = b"".join(content.encode("ascii").split())
            return base64.b64decode(normalized, validate=True)
        except (UnicodeEncodeError, binascii.Error) as e:
            raise ValueError("invalid base64 content") from e
    return content.encode()
