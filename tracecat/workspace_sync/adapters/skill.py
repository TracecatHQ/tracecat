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
    ResourceDependencyRefs,
    ResourceProjection,
    path_parts,
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
        # A companion file needs at least root + <source_id> + "files" + a
        # filename segment; anything shorter is the manifest or unrelated.
        if len(parts) < len(root_parts) + 3:
            return None
        # The leading segments must match the configured skills root exactly.
        if parts[: len(root_parts)] != root_parts:
            return None
        source_id = parts[len(root_parts)]
        files_dir = parts[len(root_parts) + 1]
        # Only paths nested under "<source_id>/files/" carry blob content; the
        # sibling "skill.yml" manifest lacks the files segment and is skipped.
        if not source_id or files_dir != SKILL_FILES_DIR:
            return None
        # Rejoin the remaining segments so nested file paths survive intact.
        file_path = "/".join(parts[len(root_parts) + 2 :])
        # Guard against a bare "files/" directory with no actual file path.
        return (source_id, file_path) if file_path else None

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize a skill's file blobs to their repository paths."""
        skill = cast(SkillResourceSpec, spec)
        # Map each file's content to its companion blob path; sort so the
        # emitted file set is deterministic across runs.
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
        # Group the flat (source_id, relpath) blob map by skill so each spec can
        # look up only its own files below.
        contents_by_source: dict[str, dict[str, str]] = defaultdict(dict)
        for (source_id, relpath), content in extra_files.items():
            contents_by_source[source_id][relpath] = content

        updated: dict[str, BaseModel] = {}
        for source_id, base_spec in specs.items():
            spec = cast(SkillResourceSpec, base_spec)
            contents = contents_by_source.get(source_id, {})
            # Validate every file the manifest declares against the blobs that
            # actually arrived from the repo.
            for file_spec in spec.files:
                content = contents.get(file_spec.path)
                # A declared file with no matching blob means the repo is
                # missing it; flag and move on without attaching content.
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
                # Re-hash the delivered blob and compare to the manifest's
                # recorded digest to catch tampering or drift.
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
            # Attach the resolved contents even when diagnostics fired so the
            # caller has whatever did parse; diagnostics gate acceptance.
            updated[source_id] = spec.model_copy(update={"file_contents": contents})
        return updated

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project skills and their current-version file blobs into Git specs."""
        stmt = self._projection_stmt(workspace_service)
        skills = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_skills(workspace_service, skills)

    async def project_dependency_refs(
        self,
        workspace_service: BaseWorkspaceService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project skills selected directly or referenced by slug."""
        if refs.select_all:
            return await self.project(workspace_service)
        if not refs.local_ids and not refs.source_ids and not refs.slugs:
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
        if local_ids and refs.slugs:
            stmt = stmt.where(
                sa.or_(Skill.id.in_(local_ids), Skill.name.in_(refs.slugs))
            )
        elif local_ids:
            stmt = stmt.where(Skill.id.in_(local_ids))
        else:
            stmt = stmt.where(Skill.name.in_(refs.slugs))
        skills = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_skills(workspace_service, skills)

    def _projection_stmt(
        self, workspace_service: BaseWorkspaceService
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
        workspace_service: BaseWorkspaceService,
        skills: list[Skill],
    ) -> ResourceProjection:
        """Build sync specs from eager-loaded skill rows."""
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for skill in skills:
            source_id = assigner.assign(skill.id, skill.name)
            # Project only the current version's files; older versions are not
            # synced to Git.
            version = skill.current_version
            files: list[SkillFileSpec] = []
            file_contents: dict[str, str] = {}
            if version is not None:
                rows = await self._skill_version_rows(workspace_service, version.id)
                for version_file, blob_row in rows:
                    # Pull the blob bytes from object storage and decode them so
                    # the spec carries the literal file content.
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
        workspace_service: BaseWorkspaceService,
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
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile skill specs into the local database.

        Upserts each skill, stores its file contents as deduplicated blobs, and
        creates or updates the target :class:`SkillVersion` (rewriting its file
        rows and recomputing the manifest hash) before pinning it as current.
        """
        skills = workspace_spec.skills
        imported: list[ImportedResource] = []
        skill_service = SkillService(
            session=workspace_service.session, role=workspace_service.role
        )
        # Sort by source id so imports apply in a deterministic order.
        for source_id, spec in sorted(skills.items()):
            # Stage 1: locate or create the skill row this spec maps to.
            skill = await self._skill_for_import(
                workspace_service, source_id=source_id, spec=spec
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

            # Stage 2: persist each file's content as a content-addressed blob,
            # deduplicating against blobs already stored for the workspace.
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

            # Stage 3: target the version named by the spec, defaulting to 1,
            # and look up whether that version already exists to update in place.
            version_number = spec.current_version or 1
            version = await workspace_service.session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_service.workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == version_number,
                )
            )
            # Build the manifest from sorted file refs so the resulting hash is
            # stable regardless of input ordering.
            manifest_payload = [
                {
                    "path": path,
                    "sha256": file_ref.blob.sha256,
                    "size_bytes": file_ref.blob.size_bytes,
                    "content_type": file_ref.content_type,
                }
                for path, file_ref in sorted(file_refs, key=lambda item: item[0])
            ]
            # The manifest digest fingerprints the whole file set for the version.
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
            # Stage 4: create the version row, or update the existing one in
            # place and clear its file rows so they can be rewritten below.
            if version is None:
                version = SkillVersion(
                    workspace_id=workspace_service.workspace_id,
                    skill_id=skill.id,
                    version=version_number,
                    **attrs,
                )
                workspace_service.session.add(version)
                # Flush to assign version.id before attaching file rows.
                await workspace_service.session.flush()
            else:
                for key, value in attrs.items():
                    setattr(version, key, value)
                # Drop stale file rows; the loop below re-adds the current set.
                await workspace_service.session.execute(
                    sa.delete(SkillVersionFile).where(
                        SkillVersionFile.workspace_id == workspace_service.workspace_id,
                        SkillVersionFile.skill_version_id == version.id,
                    )
                )

            # Stage 5: write one file row per blob, sorted for deterministic
            # insertion order.
            for path, file_ref in sorted(file_refs, key=lambda item: item[0]):
                workspace_service.session.add(
                    SkillVersionFile(
                        workspace_id=workspace_service.workspace_id,
                        skill_version_id=version.id,
                        path=path,
                        blob_id=file_ref.blob.id,
                        content_type=file_ref.content_type,
                    )
                )
            # Stage 6: pin this version as the skill's current version.
            skill.current_version_id = version.id
            workspace_service.session.add(skill)
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, skill.id))
        return imported

    async def _skill_for_import(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        spec: SkillResourceSpec,
    ) -> Skill | None:
        """Resolve the existing skill to update for ``source_id``, if any.

        Prefers the skill already mapped to ``source_id`` (validating the slug
        is still free), then falls back to matching on slug. Returns ``None``
        when a new skill must be created.
        """
        # Prefer the skill already mapped to this source id, but only after
        # confirming its incoming slug does not clash with another skill.
        skill = await self._skill_by_source_id(workspace_service, source_id=source_id)
        if skill is not None:
            await self._ensure_slug_available(
                workspace_service,
                source_id=source_id,
                slug=spec.slug,
                skill_id=skill.id,
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
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> Skill | None:
        """Load the skill mapped to ``source_id`` via the sync mapping, if any."""
        return await self._row_by_source_id(
            workspace_service, source_id=source_id, model=Skill
        )

    async def _ensure_slug_available(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        slug: str,
        skill_id: uuid.UUID,
    ) -> None:
        """Guard that ``slug`` is not already used by a different skill.

        Raises :class:`TracecatValidationError` when another skill in the
        workspace owns ``slug``.
        """
        # Look for any other skill in the workspace already holding this slug.
        conflict_id = await workspace_service.session.scalar(
            select(Skill.id).where(
                Skill.workspace_id == workspace_service.workspace_id,
                Skill.name == slug,
                Skill.id != skill_id,
            )
        )
        # No other owner: the slug is safe to assign.
        if conflict_id is None:
            return

        raise TracecatValidationError(
            f"Skill sync source id {source_id!r} cannot use slug {slug!r} "
            "because another skill already uses that slug."
        )
