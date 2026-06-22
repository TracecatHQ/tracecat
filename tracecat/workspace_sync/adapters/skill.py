"""Skill resource adapter (skill manifest plus its file blobs)."""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Mapping
from typing import cast

import orjson
import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.skill.service import SkillFileBlobRef, SkillService
from tracecat.db.models import (
    Skill,
    SkillBlob,
    SkillVersion,
    SkillVersionFile,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.service import BaseWorkspaceService
from tracecat.storage import blob
from tracecat.sync import PullDiagnostic
from tracecat.workspace_sync.adapters.base import (
    CompoundYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    path_parts,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    SKILL_ROOT,
    SkillFileSpec,
    SkillResourceSpec,
    WorkspaceManifestResources,
    WorkspaceSpec,
)

SKILL_FILENAME = "skill.yml"
SKILL_FILES_DIR = "files"


class SkillAdapter(CompoundYamlAdapter):
    """Adapter for skills: a manifest file plus its versioned file blobs."""

    resource_type = SyncResourceType.SKILL
    spec_attr = "skills"
    model = SkillResourceSpec
    root = SKILL_ROOT
    filename = SKILL_FILENAME

    def _file_source_path(self, source_id: str, file_path: str) -> str:
        """Return the repository path for a skill file under ``files/``."""
        return f"{self.root}/{source_id}/{SKILL_FILES_DIR}/{file_path}"

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
        """Map a skill file path to ``(source_id, file_path)``.

        Matches ``<root>/<source_id>/files/<file_path>`` and returns ``None``
        for anything else (including the primary manifest file).
        """
        parts = path_parts(path)
        root_parts = path_parts(roots.skills)
        if len(parts) < len(root_parts) + 3:
            return None
        if parts[: len(root_parts)] != root_parts:
            return None
        source_id = parts[len(root_parts)]
        files_dir = parts[len(root_parts) + 1]
        if not source_id or files_dir != SKILL_FILES_DIR:
            return None
        file_path = "/".join(parts[len(root_parts) + 2 :])
        return (source_id, file_path) if file_path else None

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize a skill's file blobs to their repository paths."""
        skill = cast(SkillResourceSpec, spec)
        return {
            self._file_source_path(source_id, file_path): content
            for file_path, content in sorted(skill.file_contents.items())
        }

    def attach_extra_files(
        self,
        specs: dict[str, BaseModel],
        extra_files: Mapping[tuple[str, str], str],
        diagnostics: list[PullDiagnostic],
    ) -> dict[str, BaseModel]:
        """Fold parsed skill file blobs back into each skill spec.

        Attaches file contents by source id and emits a :class:`PullDiagnostic`
        for any declared file that is missing or whose SHA256 does not match the
        manifest.
        """
        contents_by_source: dict[str, dict[str, str]] = defaultdict(dict)
        for (source_id, relpath), content in extra_files.items():
            contents_by_source[source_id][relpath] = content

        updated: dict[str, BaseModel] = {}
        for source_id, base_spec in specs.items():
            spec = cast(SkillResourceSpec, base_spec)
            contents = contents_by_source.get(source_id, {})
            for file_spec in spec.files:
                content = contents.get(file_spec.path)
                if content is None:
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=self.source_path(source_id),
                            workflow_title=spec.name,
                            error_type="dependency",
                            message=f"Skill file {file_spec.path!r} is missing",
                            details={
                                "skill_slug": spec.slug,
                                "file_path": file_spec.path,
                            },
                        )
                    )
                    continue
                actual_hash = hashlib.sha256(content.encode()).hexdigest()
                if actual_hash != file_spec.sha256:
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=self._file_source_path(
                                source_id,
                                file_spec.path,
                            ),
                            workflow_title=spec.name,
                            error_type="validation",
                            message=f"Skill file {file_spec.path!r} SHA256 does not match",
                            details={
                                "skill_slug": spec.slug,
                                "file_path": file_spec.path,
                                "expected_sha256": file_spec.sha256,
                                "actual_sha256": actual_hash,
                            },
                        )
                    )
            updated[source_id] = spec.model_copy(update={"file_contents": contents})
        return updated

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        """Project skills and their current-version file blobs into Git specs."""
        stmt = (
            select(Skill)
            .where(
                Skill.workspace_id == ctx.workspace_id,
                Skill.archived_at.is_(None),
            )
            .options(selectinload(Skill.current_version))
            .order_by(Skill.name.asc(), Skill.id.asc())
        )
        skills = list((await ctx.session.execute(stmt)).scalars().all())
        source_ids_by_local_id = await self.source_ids_by_local_id(ctx)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for skill in skills:
            source_id = source_ids_by_local_id.get(skill.id)
            if source_id is None:
                source_id = unique_source_id(skill.name, reserved=reserved)
            reserved.add(source_id)
            version = skill.current_version
            files: list[SkillFileSpec] = []
            file_contents: dict[str, str] = {}
            if version is not None:
                rows = await self._skill_version_rows(ctx, version.id)
                for version_file, blob_row in rows:
                    content = await blob.download_file(
                        key=blob_row.key,
                        bucket=blob_row.bucket,
                    )
                    files.append(
                        SkillFileSpec(
                            path=version_file.path,
                            sha256=blob_row.sha256,
                        )
                    )
                    file_contents[version_file.path] = content.decode("utf-8")

            specs[source_id] = SkillResourceSpec(
                id=source_id,
                slug=skill.name,
                name=version.name if version is not None else skill.name,
                current_version=version.version if version is not None else None,
                description=skill.description,
                files=files,
                file_contents=file_contents,
            )
            resources.append(self.projected_resource(source_id, skill.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def _skill_version_rows(
        self,
        ctx: BaseWorkspaceService,
        version_id: uuid.UUID,
    ) -> list[tuple[SkillVersionFile, SkillBlob]]:
        """Return a version's files joined to their blobs, ordered by path."""
        stmt = (
            select(SkillVersionFile, SkillBlob)
            .join(SkillBlob, SkillVersionFile.blob_id == SkillBlob.id)
            .where(
                SkillVersionFile.workspace_id == ctx.workspace_id,
                SkillVersionFile.skill_version_id == version_id,
            )
            .order_by(SkillVersionFile.path.asc())
        )
        return [
            (version_file, blob_row)
            for version_file, blob_row in (await ctx.session.execute(stmt)).all()
        ]

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile skill specs into the local database.

        Upserts each skill, stores its file contents as deduplicated blobs, and
        creates or updates the target :class:`SkillVersion` (rewriting its file
        rows and recomputing the manifest hash) before pinning it as current.
        """
        skills = workspace_spec.skills
        imported: list[ImportedResource] = []
        skill_service = SkillService(session=ctx.session, role=ctx.role)
        for source_id, spec in sorted(skills.items()):
            skill = await self._skill_for_import(ctx, source_id=source_id, spec=spec)
            if skill is None:
                skill = Skill(
                    workspace_id=ctx.workspace_id,
                    name=spec.slug,
                    description=getattr(spec, "description", None),
                    draft_revision=0,
                )
                ctx.session.add(skill)
                await ctx.session.flush()
            else:
                skill.name = spec.slug
                skill.description = getattr(spec, "description", None)

            file_refs: list[tuple[str, SkillFileBlobRef]] = []
            for file_spec in spec.files:
                content = spec.file_contents[file_spec.path].encode()
                blob_row = await skill_service._get_or_create_blob(content=content)
                file_refs.append(
                    (
                        file_spec.path,
                        SkillFileBlobRef(
                            blob=blob_row,
                            content_type=skill_service._guess_content_type(
                                file_spec.path
                            ),
                        ),
                    )
                )

            version_number = spec.current_version or 1
            version = await ctx.session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == ctx.workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == version_number,
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
            manifest_sha256 = skill_service._compute_sha256(
                orjson.dumps(manifest_payload)
            )
            attrs = {
                "manifest_sha256": manifest_sha256,
                "file_count": len(file_refs),
                "total_size_bytes": sum(
                    file_ref.blob.size_bytes for _, file_ref in file_refs
                ),
                "name": spec.name,
                "description": spec.description,
            }
            if version is None:
                version = SkillVersion(
                    workspace_id=ctx.workspace_id,
                    skill_id=skill.id,
                    version=version_number,
                    **attrs,
                )
                ctx.session.add(version)
                await ctx.session.flush()
            else:
                for key, value in attrs.items():
                    setattr(version, key, value)
                await ctx.session.execute(
                    sa.delete(SkillVersionFile).where(
                        SkillVersionFile.workspace_id == ctx.workspace_id,
                        SkillVersionFile.skill_version_id == version.id,
                    )
                )

            for path, file_ref in sorted(file_refs, key=lambda item: item[0]):
                ctx.session.add(
                    SkillVersionFile(
                        workspace_id=ctx.workspace_id,
                        skill_version_id=version.id,
                        path=path,
                        blob_id=file_ref.blob.id,
                        content_type=file_ref.content_type,
                    )
                )
            skill.current_version_id = version.id
            ctx.session.add(skill)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, skill.id))
        return imported

    async def _skill_for_import(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        spec: SkillResourceSpec,
    ) -> Skill | None:
        """Resolve the existing skill to update for ``source_id``, if any.

        Prefers the skill already mapped to ``source_id`` (validating the slug
        is still free), then falls back to matching on slug. Returns ``None``
        when a new skill must be created.
        """
        skill = await self._skill_by_source_id(ctx, source_id=source_id)
        if skill is not None:
            await self._ensure_slug_available(
                ctx,
                source_id=source_id,
                slug=spec.slug,
                skill_id=skill.id,
            )
            return skill

        return await ctx.session.scalar(
            select(Skill).where(
                Skill.workspace_id == ctx.workspace_id,
                Skill.name == spec.slug,
            )
        )

    async def _skill_by_source_id(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> Skill | None:
        """Load the skill mapped to ``source_id`` via the sync mapping, if any."""
        local_id = await self.local_id_for_source_id(ctx, source_id)
        if local_id is None:
            return None

        return await ctx.session.scalar(
            select(Skill).where(
                Skill.workspace_id == ctx.workspace_id,
                Skill.id == local_id,
            )
        )

    async def _ensure_slug_available(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        slug: str,
        skill_id: uuid.UUID,
    ) -> None:
        """Guard that ``slug`` is not already used by a different skill.

        Raises :class:`TracecatValidationError` when another skill in the
        workspace owns ``slug``.
        """
        conflict_id = await ctx.session.scalar(
            select(Skill.id).where(
                Skill.workspace_id == ctx.workspace_id,
                Skill.name == slug,
                Skill.id != skill_id,
            )
        )
        if conflict_id is None:
            return

        raise TracecatValidationError(
            f"Skill sync source id {source_id!r} cannot use slug {slug!r} "
            "because another skill already uses that slug."
        )
