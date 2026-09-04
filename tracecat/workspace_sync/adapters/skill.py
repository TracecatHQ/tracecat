"""Skill resource adapter for the current published desired state."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from collections import defaultdict
from collections.abc import Mapping
from typing import Literal, cast

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.skill.service import (
    ManifestValidationResult,
    SkillFileBlobRef,
    SkillService,
)
from tracecat.db.models import (
    Skill,
    SkillBlob,
    SkillVersion,
    SkillVersionFile,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.storage import blob
from tracecat.sync import PullDiagnostic
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
    WorkspaceManifestResources,
    WorkspaceSpec,
)

SKILL_FILENAME = "skill.yml"
SKILL_FILES_DIR = "files"


class SkillAdapter(DirectoryManifestAdapter):
    """Adapter for a Skill head manifest and its published file contents."""

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

    def _file_source_path(self, source_id: str, file_path: str) -> str:
        """Return the repository path for desired Skill-head file content."""
        return f"{self.root}/{source_id}/{SKILL_FILES_DIR}/{file_path}"

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
        """Map desired head file content to ``(source_id, relpath)``."""
        parts = path_parts(path)
        root_parts = path_parts(roots.skills)
        # The leading segments must match the configured skills root exactly.
        if parts[: len(root_parts)] != root_parts:
            return None
        if len(parts) < len(root_parts) + 3:
            return None
        source_id = parts[len(root_parts)]
        if not source_id or parts[len(root_parts) + 1] != SKILL_FILES_DIR:
            return None
        relpath = "/".join(parts[len(root_parts) + 1 :])
        return (source_id, relpath) if relpath else None

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize the desired Skill-head file contents."""
        skill = cast(SkillResourceSpec, spec)
        return {
            f"{self.root}/{source_id}/{SKILL_FILES_DIR}/{file_path}": content
            for file_path, content in sorted(skill.file_contents.items())
        }

    def attach_extra_files(
        self,
        specs: dict[str, BaseModel],
        extra_files: Mapping[tuple[str, str], str],
        diagnostics: list[PullDiagnostic],
    ) -> dict[str, BaseModel]:
        """Attach and validate desired Skill-head file contents."""
        contents_by_source: dict[str, dict[str, str]] = defaultdict(dict)
        for (source_id, relpath), content in extra_files.items():
            parts = path_parts(relpath)
            if len(parts) < 2 or parts[0] != SKILL_FILES_DIR:
                continue
            contents_by_source[source_id]["/".join(parts[1:])] = content

        updated: dict[str, BaseModel] = {}
        for source_id, base_spec in specs.items():
            spec = cast(SkillResourceSpec, base_spec)
            contents = contents_by_source.get(source_id, {})
            self._validate_head_files(
                source_id=source_id,
                spec=spec,
                contents=contents,
                diagnostics=diagnostics,
            )
            updated[source_id] = spec.model_copy(
                update={
                    "file_contents": contents,
                }
            )
        return updated

    def _validate_head_files(
        self,
        *,
        source_id: str,
        spec: SkillResourceSpec,
        contents: Mapping[str, str],
        diagnostics: list[PullDiagnostic],
    ) -> None:
        """Validate a parsed Skill head's declared file hashes."""
        for file_spec in spec.files:
            content = contents.get(file_spec.path)
            if content is None:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=self.source_path(source_id),
                        workflow_title=spec.name,
                        error_type="dependency",
                        message=(
                            f"Skill {spec.slug!r} head file "
                            f"{file_spec.path!r} is missing"
                        ),
                        details={
                            "skill_slug": spec.slug,
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
                        workflow_path=self._file_source_path(source_id, file_spec.path),
                        workflow_title=spec.name,
                        error_type="validation",
                        message=(
                            f"Skill {spec.slug!r} head file {file_spec.path!r} "
                            f"could not be decoded: {e}"
                        ),
                        details={
                            "skill_slug": spec.slug,
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
                        workflow_path=self._file_source_path(source_id, file_spec.path),
                        workflow_title=spec.name,
                        error_type="validation",
                        message=(
                            f"Skill {spec.slug!r} head file {file_spec.path!r} "
                            "SHA256 does not match"
                        ),
                        details={
                            "skill_slug": spec.slug,
                            "file_path": file_spec.path,
                            "expected_sha256": file_spec.sha256,
                            "actual_sha256": actual_hash,
                        },
                    )
                )

    async def project(
        self, workspace_service: SyncMappingService
    ) -> ResourceProjection:
        """Project each Skill's current desired head content."""
        stmt = self._projection_stmt(workspace_service)
        skills = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_skills(workspace_service, skills)

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
        slug_column = sa.func.coalesce(Skill.slug, Skill.name)
        if local_ids and slugs:
            stmt = stmt.where(sa.or_(Skill.id.in_(local_ids), slug_column.in_(slugs)))
        elif local_ids:
            stmt = stmt.where(Skill.id.in_(local_ids))
        else:
            stmt = stmt.where(slug_column.in_(slugs))
        skills = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_skills(workspace_service, skills)

    def _projection_stmt(
        self, workspace_service: SyncMappingService
    ) -> sa.Select[tuple[Skill]]:
        """Build the base eager-loaded skill projection query."""
        return (
            select(Skill)
            .where(
                Skill.workspace_id == workspace_service.workspace_id,
                # Expand-window check: legacy writers set only archived_at; the
                # contract release drops the archived_at leg.
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            .options(selectinload(Skill.current_version))
            .order_by(Skill.name.asc(), Skill.id.asc())
        )

    async def _projection_from_skills(
        self,
        workspace_service: SyncMappingService,
        skills: list[Skill],
    ) -> ResourceProjection:
        """Build one desired head spec from each current Skill version."""
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for skill in skills:
            version = skill.current_version
            if version is None:
                continue
            skill_slug = skill.slug or skill.name
            source_id = assigner.assign(skill.id, skill_slug)
            files: list[SkillFileSpec] = []
            file_contents: dict[str, str] = {}
            for version_file, blob_row in await self._skill_version_rows(
                workspace_service,
                version.id,
            ):
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

            specs[source_id] = SkillResourceSpec(
                id=source_id,
                slug=skill_slug,
                name=version.name,
                description=skill.description,
                files=files,
                file_contents=file_contents,
            )
            resources.append(self.projected_resource(source_id, skill.id))
        return ResourceProjection(specs=specs, resources=resources)

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
            name_column=Skill.slug,
            noun="slug",
            kind_label="Skill",
            owner_label="skill",
            error_cls=TracecatValidationError,
            temp_prefix="__tc_sync_tmp_",
            row_predicates=(Skill.deleted_at.is_(None), Skill.archived_at.is_(None)),
            availability_predicates=(
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            ),
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
                await skill_service._validate_skill_slug_available(spec.slug)
                skill = Skill(
                    workspace_id=workspace_service.workspace_id,
                    name=spec.name,
                    slug=spec.slug,
                    description=spec.description,
                    draft_revision=0,
                )
                workspace_service.session.add(skill)
                # Flush to assign skill.id before referencing it below.
                await workspace_service.session.flush()
            else:
                skill.slug = spec.slug
                skill.name = spec.name
                skill.description = spec.description

            current = None
            if skill.current_version_id is not None:
                current = await workspace_service.session.scalar(
                    select(SkillVersion).where(
                        SkillVersion.workspace_id == workspace_service.workspace_id,
                        SkillVersion.skill_id == skill.id,
                        SkillVersion.id == skill.current_version_id,
                    )
                )
            if current is None or not await self._version_matches_desired(
                workspace_service,
                current=current,
                desired=spec,
            ):
                prior_file_refs = (
                    await self._file_refs_for_version(
                        workspace_service,
                        current.id,
                    )
                    if current is not None
                    else None
                )
                file_refs = await self._materialize_file_refs(
                    skill_service,
                    spec,
                    prior_file_refs=prior_file_refs,
                )
                current = await skill_service.publish_version_from_blob_refs(
                    skill=skill,
                    file_refs=list(file_refs.items()),
                    validation=ManifestValidationResult(
                        name=spec.name,
                        description=spec.description,
                    ),
                    head_name=spec.name,
                )
            else:
                file_refs = await self._file_refs_for_version(
                    workspace_service,
                    current.id,
                )
            skill.current_version_id = current.id
            await skill_service._replace_draft_with_blob_map(
                skill=skill,
                path_to_blob=file_refs,
            )
            workspace_service.session.add(skill)
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, skill.id))
        return imported

    async def _version_matches_desired(
        self,
        workspace_service: SyncMappingService,
        *,
        current: SkillVersion,
        desired: SkillResourceSpec,
    ) -> bool:
        """Return whether the current immutable version matches desired Git state."""

        if current.name != desired.name or current.description != desired.description:
            return False
        rows = await self._skill_version_rows(workspace_service, current.id)
        current_files = {
            version_file.path: blob_row.sha256 for version_file, blob_row in rows
        }
        desired_files = {file.path: file.sha256 for file in desired.files}
        return current_files == desired_files

    async def _file_refs_for_version(
        self,
        workspace_service: SyncMappingService,
        version_id: uuid.UUID,
    ) -> dict[str, SkillFileBlobRef]:
        """Load immutable file refs for resetting the mutable draft shadow."""

        return {
            version_file.path: SkillFileBlobRef(
                blob=blob_row,
                content_type=version_file.content_type,
            )
            for version_file, blob_row in await self._skill_version_rows(
                workspace_service, version_id
            )
        }

    async def _materialize_file_refs(
        self,
        skill_service: SkillService,
        spec: SkillResourceSpec,
        *,
        prior_file_refs: Mapping[str, SkillFileBlobRef] | None = None,
    ) -> dict[str, SkillFileBlobRef]:
        """Materialize a Git-owned Skill head's declared file blobs."""

        file_refs: dict[str, SkillFileBlobRef] = {}
        for file_spec in spec.files:
            content_text = spec.file_contents.get(file_spec.path)
            if content_text is None:
                raise TracecatValidationError(
                    f"Skill {spec.slug!r} head "
                    f"declares file {file_spec.path!r} but no content was provided."
                )
            try:
                content = _skill_file_content_bytes(file_spec, content_text)
            except ValueError as e:
                raise TracecatValidationError(
                    f"Skill {spec.slug!r} head "
                    f"file {file_spec.path!r} could not be decoded: {e}"
                ) from e
            prior_ref = (
                prior_file_refs.get(file_spec.path)
                if prior_file_refs is not None
                else None
            )
            if prior_ref is not None and prior_ref.blob.sha256 == file_spec.sha256:
                file_refs[file_spec.path] = prior_ref
                continue
            blob_row = await skill_service._get_or_create_blob(content=content)
            file_refs[file_spec.path] = SkillFileBlobRef(
                blob=blob_row,
                content_type=skill_service._guess_content_type(file_spec.path),
            )
        return file_refs

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
                sa.or_(
                    Skill.slug == spec.slug,
                    sa.and_(Skill.slug.is_(None), Skill.name == spec.slug),
                ),
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
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
            workspace_service,
            source_id=source_id,
            model=Skill,
            row_predicates=(Skill.deleted_at.is_(None), Skill.archived_at.is_(None)),
        )


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
