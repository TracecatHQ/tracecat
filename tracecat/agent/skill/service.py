"""Service layer for workspace skills."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

import orjson
import sqlalchemy as sa
import yaml
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat import config
from tracecat.agent.preset.schemas import AgentPresetSkillBindingBase
from tracecat.agent.skill.schemas import (
    SkillCreate,
    SkillDraftAttachUploadedBlobOp,
    SkillDraftDeleteFileOp,
    SkillDraftFileRead,
    SkillDraftPatch,
    SkillDraftRead,
    SkillDraftUpsertTextFileOp,
    SkillFileEntry,
    SkillRead,
    SkillUpload,
    SkillUploadSessionCreate,
    SkillUploadSessionRead,
    SkillValidationErrorDetail,
    SkillVersionRead,
    SkillVersionSummary,
)
from tracecat.agent.skill.types import ResolvedSkillRef
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentPresetSkill,
    AgentPresetVersionSkill,
    Skill,
    SkillBlob,
    SkillDraftFile,
    SkillVersion,
    SkillVersionFile,
)
from tracecat.db.models import (
    SkillUpload as SkillUploadModel,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.storage import blob
from tracecat.tiers.enums import Entitlement

INLINE_TEXT_LIMIT_BYTES = 256 * 1024
DEFAULT_UPLOAD_TTL_SECONDS = 15 * 60


@dataclass(slots=True)
class ManifestValidationResult:
    """Result of validating a skill draft or published manifest."""

    title: str | None = None
    description: str | None = None
    errors: list[SkillValidationErrorDetail] = field(default_factory=list)


class SkillService(BaseWorkspaceService):
    """CRUD operations and execution helpers for workspace skills."""

    service_name = "skill"

    @staticmethod
    def _compute_sha256(content: bytes) -> str:
        """Return the SHA256 digest for a skill blob."""

        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _normalize_content_type(content_type: str) -> str:
        """Return a canonical MIME type string for storage key derivation."""

        parts = [part.strip().lower() for part in content_type.split(";")]
        return "; ".join(part for part in parts if part)

    @classmethod
    def _content_type_digest(cls, content_type: str) -> str:
        """Return a short stable digest for a canonicalized MIME type."""

        normalized = cls._normalize_content_type(content_type)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _storage_key_for(self, sha256: str, content_type: str) -> str:
        """Return the canonical storage key for a skill blob."""

        content_type_digest = self._content_type_digest(content_type)
        return f"skills/{self.workspace_id}/{sha256}/{content_type_digest}"

    def _staged_upload_key_for(self, *, upload_id: uuid.UUID, sha256: str) -> str:
        """Return the temporary storage key for a staged skill upload."""

        return f"skills/{self.workspace_id}/uploads/{upload_id}/{sha256}"

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize and validate a relative POSIX draft path.

        Args:
            path: User-provided file path.

        Returns:
            The normalized relative POSIX path.

        Raises:
            TracecatValidationError: If the path is empty, absolute, or escapes
                the skill root.
        """

        if "\\" in path:
            raise TracecatValidationError(
                f"Skill paths must use POSIX separators: {path!r}",
                detail={"code": "invalid_path", "path": path},
            )

        path_obj = PurePosixPath(path)
        normalized = str(path_obj)
        if normalized in {"", "."}:
            raise TracecatValidationError(
                "Skill path cannot be empty",
                detail={"code": "invalid_path", "path": path},
            )
        if path_obj.is_absolute() or ".." in path_obj.parts:
            raise TracecatValidationError(
                f"Skill path cannot escape the skill root: {path!r}",
                detail={"code": "invalid_path", "path": path},
            )
        if normalized != path:
            raise TracecatValidationError(
                f"Skill path must already be normalized: {path!r}",
                detail={"code": "invalid_path", "path": path},
            )
        return normalized

    @staticmethod
    def _guess_content_type(path: str) -> str:
        """Infer a content type for a skill file path."""

        if path.endswith(".md"):
            return "text/markdown; charset=utf-8"
        if guessed := mimetypes.guess_type(path)[0]:
            if guessed.startswith("text/"):
                return f"{guessed}; charset=utf-8"
            return guessed
        return "application/octet-stream"

    @staticmethod
    def _is_inline_text(content_type: str, *, size_bytes: int) -> bool:
        """Return whether a file should be returned inline as UTF-8."""

        if size_bytes > INLINE_TEXT_LIMIT_BYTES:
            return False
        mime_type = content_type.split(";", 1)[0].strip().lower()
        return mime_type.startswith("text/") or mime_type in {
            "application/json",
            "application/xml",
            "application/yaml",
            "application/x-yaml",
        }

    @staticmethod
    def _build_default_skill_markdown(
        *, slug: str, title: str | None, description: str | None
    ) -> str:
        """Create the seeded root SKILL.md for a new skill."""

        resolved_title = title or slug.replace("-", " ").title()
        metadata: dict[str, str] = {"title": resolved_title}
        if description:
            metadata["description"] = description
        frontmatter_yaml = yaml.safe_dump(
            metadata,
            sort_keys=False,
        ).strip()
        return "\n".join(
            [
                "---",
                frontmatter_yaml,
                "---",
                "",
                f"# {resolved_title}",
                "",
                "Describe when this skill should be used and what it does.",
            ]
        )

    @staticmethod
    def _is_skill_slug_conflict_error(exc: IntegrityError) -> bool:
        """Return whether an integrity error came from the skill slug unique key."""

        constraint_name = getattr(
            getattr(exc.orig, "diag", None), "constraint_name", ""
        )
        return constraint_name == "uq_skill_workspace_slug" or (
            "uq_skill_workspace_slug" in str(exc)
        )

    @staticmethod
    def _raise_skill_slug_conflict(
        slug: str, *, from_error: Exception | None = None
    ) -> None:
        """Raise the canonical validation error for duplicate skill slugs."""

        raise TracecatValidationError(
            f"Skill slug '{slug}' is already in use for this workspace",
            detail={"code": "skill_slug_conflict", "slug": slug},
        ) from from_error

    @staticmethod
    def _merge_skill_markdown_metadata(
        skill_markdown: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> str:
        """Merge title/description frontmatter into an existing SKILL.md body."""

        metadata: dict[str, object] = {}
        body = skill_markdown

        if skill_markdown.startswith("---\n"):
            _, _, remainder = skill_markdown.partition("---\n")
            frontmatter, separator, body_part = remainder.partition("\n---\n")
            if separator:
                loaded = yaml.safe_load(frontmatter) or {}
                if isinstance(loaded, dict):
                    metadata = dict(loaded)
                    body = body_part

        if title is not None:
            metadata["title"] = title
        if description is not None:
            metadata["description"] = description

        frontmatter_yaml = yaml.safe_dump(
            metadata,
            sort_keys=False,
        ).strip()
        if not body:
            return f"---\n{frontmatter_yaml}\n---\n"
        if body.startswith("\n"):
            return f"---\n{frontmatter_yaml}\n---{body}"
        return f"---\n{frontmatter_yaml}\n---\n\n{body}"

    @staticmethod
    def _extract_frontmatter(skill_markdown: str) -> tuple[str | None, str | None]:
        """Extract title and description from root SKILL.md frontmatter.

        Raises:
            TracecatValidationError: If the frontmatter contains invalid YAML.
        """

        if not skill_markdown.startswith("---\n"):
            return None, None
        _, _, remainder = skill_markdown.partition("---\n")
        frontmatter, separator, _ = remainder.partition("\n---\n")
        if not separator:
            return None, None
        try:
            loaded = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError as exc:
            raise TracecatValidationError(
                "Root SKILL.md frontmatter must be valid YAML",
                detail={"code": "invalid_skill_md_frontmatter", "path": "SKILL.md"},
            ) from exc
        if not isinstance(loaded, dict):
            return None, None
        title = loaded.get("title")
        description = loaded.get("description")
        return (
            title if isinstance(title, str) and title.strip() else None,
            description
            if isinstance(description, str) and description.strip()
            else None,
        )

    async def _get_or_create_blob(
        self, *, content: bytes, content_type: str
    ) -> SkillBlob:
        """Create or reuse a content-addressed skill blob.

        Args:
            content: Blob payload.
            content_type: Blob MIME type.

        Returns:
            The deduplicated blob row.
        """

        sha256 = self._compute_sha256(content)
        stmt = select(SkillBlob).where(
            SkillBlob.workspace_id == self.workspace_id,
            SkillBlob.sha256 == sha256,
            SkillBlob.content_type == content_type,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        storage_key = self._storage_key_for(sha256, content_type)
        await blob.upload_file(
            content=content,
            key=storage_key,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            content_type=content_type,
        )
        created = SkillBlob(
            workspace_id=self.workspace_id,
            sha256=sha256,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            key=storage_key,
            size_bytes=len(content),
            content_type=content_type,
        )
        self.session.add(created)
        await self.session.flush()
        return created

    async def _materialize_uploaded_blob(self, upload: SkillUploadModel) -> SkillBlob:
        """Finalize a staged upload into a reusable blob row."""

        if upload.completed_at is not None and upload.blob_id is not None:
            blob_row = await self.get_blob(upload.blob_id)
            if blob_row is None:
                raise TracecatNotFoundError(f"Skill blob '{upload.blob_id}' not found")
            return blob_row

        if upload.expires_at < datetime.now(UTC):
            raise TracecatValidationError(
                "Skill upload session has expired",
                detail={"code": "upload_expired", "upload_id": str(upload.id)},
            )
        if not await blob.file_exists(key=upload.key, bucket=upload.bucket):
            raise TracecatValidationError(
                "Uploaded blob was not found in object storage",
                detail={"code": "upload_missing", "upload_id": str(upload.id)},
            )

        content = await blob.download_file(key=upload.key, bucket=upload.bucket)
        actual_size_bytes = len(content)
        actual_sha256 = self._compute_sha256(content)
        if actual_sha256 != upload.sha256:
            raise TracecatValidationError(
                "Uploaded blob SHA-256 mismatch",
                detail={
                    "code": "upload_integrity_error",
                    "upload_id": str(upload.id),
                },
            )
        if actual_size_bytes != upload.size_bytes:
            raise TracecatValidationError(
                "Uploaded blob size mismatch",
                detail={
                    "code": "upload_integrity_error",
                    "upload_id": str(upload.id),
                },
            )

        blob_row = (
            await self.session.execute(
                select(SkillBlob).where(
                    SkillBlob.workspace_id == self.workspace_id,
                    SkillBlob.sha256 == upload.sha256,
                    SkillBlob.content_type == upload.content_type,
                )
            )
        ).scalar_one_or_none()
        if blob_row is None:
            canonical_key = self._storage_key_for(upload.sha256, upload.content_type)
            if upload.key != canonical_key:
                await blob.upload_file(
                    content=content,
                    key=canonical_key,
                    bucket=upload.bucket,
                    content_type=upload.content_type,
                )
            blob_row = SkillBlob(
                workspace_id=self.workspace_id,
                sha256=upload.sha256,
                bucket=upload.bucket,
                key=canonical_key,
                size_bytes=actual_size_bytes,
                content_type=upload.content_type,
            )
            self.session.add(blob_row)
            await self.session.flush()

        upload.blob_id = blob_row.id
        upload.completed_at = datetime.now(UTC)
        self.session.add(upload)
        await self.session.flush()
        return blob_row

    async def _list_draft_rows(
        self, skill_id: uuid.UUID
    ) -> list[tuple[SkillDraftFile, SkillBlob]]:
        """Return the current draft manifest rows joined with blobs."""

        stmt = (
            select(SkillDraftFile, SkillBlob)
            .join(SkillBlob, SkillDraftFile.blob_id == SkillBlob.id)
            .where(
                SkillDraftFile.workspace_id == self.workspace_id,
                SkillDraftFile.skill_id == skill_id,
            )
            .order_by(SkillDraftFile.path.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.tuples().all())

    async def _list_version_rows(
        self, skill_version_id: uuid.UUID
    ) -> list[tuple[SkillVersionFile, SkillBlob]]:
        """Return the published manifest rows joined with blobs."""

        stmt = (
            select(SkillVersionFile, SkillBlob)
            .join(SkillBlob, SkillVersionFile.blob_id == SkillBlob.id)
            .where(
                SkillVersionFile.workspace_id == self.workspace_id,
                SkillVersionFile.skill_version_id == skill_version_id,
            )
            .order_by(SkillVersionFile.path.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.tuples().all())

    async def _validate_manifest_rows(
        self, rows: Sequence[tuple[str, SkillBlob]]
    ) -> ManifestValidationResult:
        """Validate a draft or published manifest."""

        result = ManifestValidationResult()
        seen_paths: set[str] = set()
        skill_md_blob: SkillBlob | None = None

        for path, blob_row in rows:
            try:
                normalized = self._normalize_path(path)
            except TracecatValidationError as exc:
                result.errors.append(
                    SkillValidationErrorDetail(
                        code="invalid_path",
                        message=str(exc),
                        path=path,
                    )
                )
                continue
            if normalized in seen_paths:
                result.errors.append(
                    SkillValidationErrorDetail(
                        code="duplicate_path",
                        message=f"Duplicate skill path {normalized!r}",
                        path=normalized,
                    )
                )
            seen_paths.add(normalized)
            if normalized == "SKILL.md":
                skill_md_blob = blob_row

        if skill_md_blob is None:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="missing_root_skill_md",
                    message="Root SKILL.md is required",
                    path="SKILL.md",
                )
            )
            return result

        try:
            content = await blob.download_file(
                key=skill_md_blob.key,
                bucket=skill_md_blob.bucket,
            )
            markdown = content.decode("utf-8")
        except UnicodeDecodeError:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="invalid_skill_md_encoding",
                    message="Root SKILL.md must be UTF-8 text",
                    path="SKILL.md",
                )
            )
            return result

        try:
            result.title, result.description = self._extract_frontmatter(markdown)
        except TracecatValidationError as exc:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="invalid_skill_md_frontmatter",
                    message=str(exc),
                    path="SKILL.md",
                )
            )
        return result

    async def _build_draft_read(self, skill: Skill) -> SkillDraftRead:
        """Build the current draft response for a skill."""

        rows = await self._list_draft_rows(skill.id)
        file_entries: list[SkillFileEntry] = []
        validation_pairs: list[tuple[str, SkillBlob]] = []
        for draft_file, blob_row in rows:
            file_entries.append(
                SkillFileEntry(
                    path=draft_file.path,
                    blob_id=blob_row.id,
                    sha256=blob_row.sha256,
                    size_bytes=blob_row.size_bytes,
                    content_type=blob_row.content_type,
                )
            )
            validation_pairs.append((draft_file.path, blob_row))
        validation = await self._validate_manifest_rows(validation_pairs)
        return SkillDraftRead(
            skill_id=skill.id,
            skill_slug=skill.slug,
            draft_revision=skill.draft_revision,
            title=validation.title,
            description=validation.description,
            files=file_entries,
            is_publishable=not validation.errors,
            validation_errors=validation.errors,
        )

    async def _build_skill_read(self, skill: Skill) -> SkillRead:
        """Build the summary response for a skill."""

        draft = await self._build_draft_read(skill)
        current_version_summary = None
        current_version = None
        if skill.current_version_id is not None:
            current_version = await self.get_version(skill.current_version_id)
        if current_version is not None:
            current_version_summary = SkillVersionSummary(
                id=current_version.id,
                version=current_version.version,
                manifest_sha256=current_version.manifest_sha256,
                file_count=current_version.file_count,
                total_size_bytes=current_version.total_size_bytes,
                title=current_version.title,
                description=current_version.description,
                created_at=current_version.created_at,
                updated_at=current_version.updated_at,
            )
        return SkillRead(
            id=skill.id,
            workspace_id=skill.workspace_id,
            slug=skill.slug,
            title=skill.title,
            description=skill.description,
            current_version_id=skill.current_version_id,
            draft_revision=skill.draft_revision,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
            archived_at=skill.archived_at,
            current_version=current_version_summary,
            is_draft_publishable=draft.is_publishable,
            draft_validation_errors=draft.validation_errors,
            draft_file_count=len(draft.files),
        )

    async def _replace_draft_with_blob_map(
        self, *, skill: Skill, path_to_blob: dict[str, SkillBlob]
    ) -> None:
        """Replace the draft manifest with a new set of blob references."""

        await self.session.execute(
            sa.delete(SkillDraftFile).where(
                SkillDraftFile.workspace_id == self.workspace_id,
                SkillDraftFile.skill_id == skill.id,
            )
        )
        for path, blob_row in sorted(path_to_blob.items()):
            self.session.add(
                SkillDraftFile(
                    workspace_id=self.workspace_id,
                    skill_id=skill.id,
                    path=path,
                    blob_id=blob_row.id,
                )
            )
        skill.draft_revision += 1
        await self.session.flush()

    async def get_blob(self, blob_id: uuid.UUID) -> SkillBlob | None:
        """Return a blob row by ID."""

        stmt = select(SkillBlob).where(
            SkillBlob.workspace_id == self.workspace_id,
            SkillBlob.id == blob_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_skill(self, skill_id: uuid.UUID) -> Skill | None:
        """Return a skill by ID."""

        stmt = (
            select(Skill)
            .options(selectinload(Skill.current_version))
            .where(
                Skill.workspace_id == self.workspace_id,
                Skill.id == skill_id,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_skill_for_update(self, skill_id: uuid.UUID) -> Skill | None:
        """Return and lock a skill row for mutation."""

        stmt = (
            select(Skill)
            .where(
                Skill.workspace_id == self.workspace_id,
                Skill.id == skill_id,
            )
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_bindable_skills(
        self,
        skill_ids: Sequence[uuid.UUID],
        *,
        for_update: bool = False,
    ) -> dict[uuid.UUID, Skill]:
        """Return active skills that can be bound onto a preset.

        When ``for_update`` is true, rows are locked in a deterministic order so
        skill archival and preset binding writes serialize on the same records.
        """

        normalized_ids = sorted(set(skill_ids), key=str)
        if not normalized_ids:
            return {}

        if not for_update:
            stmt = select(Skill).where(
                Skill.workspace_id == self.workspace_id,
                Skill.id.in_(normalized_ids),
                Skill.archived_at.is_(None),
            )
            return {
                skill.id: skill
                for skill in (await self.session.execute(stmt)).scalars().all()
            }

        skills: dict[uuid.UUID, Skill] = {}
        for skill_id in normalized_ids:
            stmt = (
                select(Skill)
                .where(
                    Skill.workspace_id == self.workspace_id,
                    Skill.id == skill_id,
                    Skill.archived_at.is_(None),
                )
                .with_for_update()
            )
            if skill := (await self.session.execute(stmt)).scalar_one_or_none():
                skills[skill.id] = skill
        return skills

    async def _normalize_and_validate_slug(
        self, *, slug: str, exclude_id: uuid.UUID | None = None
    ) -> str:
        """Validate skill slug uniqueness within the workspace."""

        normalized = slugify(slug, separator="-")
        if not normalized:
            raise TracecatValidationError(
                "Skill slug cannot be empty",
                detail={"code": "invalid_slug"},
            )
        stmt = select(Skill.id).where(
            Skill.workspace_id == self.workspace_id,
            Skill.slug == normalized,
        )
        if exclude_id is not None:
            stmt = stmt.where(Skill.id != exclude_id)
        if (await self.session.execute(stmt)).scalar_one_or_none() is not None:
            self._raise_skill_slug_conflict(normalized)
        return normalized

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_skill(self, params: SkillCreate) -> SkillRead:
        """Create a logical skill and seed its initial draft.

        Args:
            params: Skill creation payload.

        Returns:
            The created skill summary.
        """

        slug = await self._normalize_and_validate_slug(slug=params.slug)
        skill = Skill(workspace_id=self.workspace_id, slug=slug, draft_revision=0)
        self.session.add(skill)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            if self._is_skill_slug_conflict_error(exc):
                self._raise_skill_slug_conflict(slug, from_error=exc)
            raise
        root_markdown = self._build_default_skill_markdown(
            slug=slug,
            title=params.title,
            description=params.description,
        )
        root_blob = await self._get_or_create_blob(
            content=root_markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        await self._replace_draft_with_blob_map(
            skill=skill,
            path_to_blob={"SKILL.md": root_blob},
        )
        await self.session.commit()
        await self.session.refresh(skill)
        return await self._build_skill_read(skill)

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def upload_skill(self, params: SkillUpload) -> SkillRead:
        """Import a full skill draft in one operation.

        Args:
            params: Uploaded skill file tree.

        Returns:
            The created skill summary.
        """

        slug = await self._normalize_and_validate_slug(slug=params.slug)
        path_to_blob: dict[str, SkillBlob] = {}
        for file_payload in params.files:
            path = self._normalize_path(file_payload.path)
            if path in path_to_blob:
                raise TracecatValidationError(
                    f"Duplicate skill path {path!r}",
                    detail={"code": "duplicate_path", "path": path},
                )
            try:
                content = base64.b64decode(file_payload.content_base64, validate=True)
            except ValueError as exc:
                raise TracecatValidationError(
                    f"Invalid base64 content for skill path {path!r}",
                    detail={"code": "invalid_base64", "path": path},
                ) from exc
            path_to_blob[path] = await self._get_or_create_blob(
                content=content,
                content_type=file_payload.content_type
                or self._guess_content_type(path),
            )

        skill = Skill(workspace_id=self.workspace_id, slug=slug, draft_revision=0)
        self.session.add(skill)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            if self._is_skill_slug_conflict_error(exc):
                self._raise_skill_slug_conflict(slug, from_error=exc)
            raise
        await self._replace_draft_with_blob_map(skill=skill, path_to_blob=path_to_blob)
        await self.session.commit()
        await self.session.refresh(skill)
        return await self._build_skill_read(skill)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_skills(
        self, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[SkillRead]:
        """List workspace skills with cursor pagination."""

        paginator = BaseCursorPaginator(self.session)
        stmt = (
            select(Skill)
            .options(selectinload(Skill.current_version))
            .where(Skill.workspace_id == self.workspace_id)
        )
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError("Invalid cursor for skills") from err
            cursor_updated_at = cursor_data.sort_value
            if not isinstance(cursor_updated_at, datetime):
                raise TracecatValidationError("Invalid cursor for skills")
            predicate = sa.or_(
                Skill.updated_at < cursor_updated_at,
                sa.and_(Skill.updated_at == cursor_updated_at, Skill.id < cursor_id),
            )
            if params.reverse:
                predicate = sa.or_(
                    Skill.updated_at > cursor_updated_at,
                    sa.and_(
                        Skill.updated_at == cursor_updated_at, Skill.id > cursor_id
                    ),
                )
            stmt = stmt.where(predicate)

        if params.reverse:
            stmt = stmt.order_by(Skill.updated_at.asc(), Skill.id.asc())
        else:
            stmt = stmt.order_by(Skill.updated_at.desc(), Skill.id.desc())
        stmt = stmt.limit(params.limit + 1)
        skills = (await self.session.execute(stmt)).scalars().all()
        has_more = len(skills) > params.limit
        items = skills[: params.limit]

        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = paginator.encode_cursor(
                last.id,
                sort_column="updated_at",
                sort_value=last.updated_at,
            )

        prev_cursor = None
        if params.cursor and items:
            first = items[0]
            prev_cursor = paginator.encode_cursor(
                first.id,
                sort_column="updated_at",
                sort_value=first.updated_at,
            )

        return CursorPaginatedResponse(
            items=[await self._build_skill_read(skill) for skill in items],
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=params.cursor is not None,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_skill_read(self, skill_id: uuid.UUID) -> SkillRead | None:
        """Return a fully rendered skill summary."""

        if (skill := await self.get_skill(skill_id)) is None:
            return None
        return await self._build_skill_read(skill)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_draft(self, skill_id: uuid.UUID) -> SkillDraftRead | None:
        """Return the current mutable draft for a skill."""

        if (skill := await self.get_skill(skill_id)) is None:
            return None
        return await self._build_draft_read(skill)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_draft_file(
        self, *, skill_id: uuid.UUID, path: str
    ) -> SkillDraftFileRead | None:
        """Return one draft file either inline or as a presigned download."""

        normalized_path = self._normalize_path(path)
        stmt = (
            select(SkillDraftFile, SkillBlob)
            .join(SkillBlob, SkillDraftFile.blob_id == SkillBlob.id)
            .where(
                SkillDraftFile.workspace_id == self.workspace_id,
                SkillDraftFile.skill_id == skill_id,
                SkillDraftFile.path == normalized_path,
            )
        )
        row = (await self.session.execute(stmt)).tuples().first()
        if row is None:
            return None
        _, blob_row = row
        if self._is_inline_text(blob_row.content_type, size_bytes=blob_row.size_bytes):
            try:
                content = await blob.download_file(
                    key=blob_row.key,
                    bucket=blob_row.bucket,
                )
                return SkillDraftFileRead(
                    kind="inline",
                    path=normalized_path,
                    content_type=blob_row.content_type,
                    size_bytes=blob_row.size_bytes,
                    sha256=blob_row.sha256,
                    text_content=content.decode("utf-8"),
                )
            except UnicodeDecodeError:
                pass

        return SkillDraftFileRead(
            kind="download",
            path=normalized_path,
            content_type=blob_row.content_type,
            size_bytes=blob_row.size_bytes,
            sha256=blob_row.sha256,
            download_url=await blob.generate_presigned_download_url(
                key=blob_row.key,
                bucket=blob_row.bucket,
            ),
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def patch_draft(
        self, *, skill_id: uuid.UUID, params: SkillDraftPatch
    ) -> SkillDraftRead:
        """Apply optimistic-concurrency mutations to a skill draft."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        if skill.draft_revision != params.base_revision:
            raise TracecatValidationError(
                "Draft revision conflict",
                detail={
                    "code": "draft_revision_conflict",
                    "current_revision": skill.draft_revision,
                },
            )

        current_rows = await self._list_draft_rows(skill.id)
        path_to_blob = {
            draft_file.path: blob_row for draft_file, blob_row in current_rows
        }
        for operation in params.operations:
            match operation:
                case SkillDraftUpsertTextFileOp():
                    normalized_path = self._normalize_path(operation.path)
                    blob_row = await self._get_or_create_blob(
                        content=operation.content.encode("utf-8"),
                        content_type=operation.content_type,
                    )
                    path_to_blob[normalized_path] = blob_row
                case SkillDraftAttachUploadedBlobOp():
                    normalized_path = self._normalize_path(operation.path)
                    upload_stmt = select(SkillUploadModel).where(
                        SkillUploadModel.workspace_id == self.workspace_id,
                        SkillUploadModel.skill_id == skill.id,
                        SkillUploadModel.id == operation.upload_id,
                    )
                    upload = (
                        await self.session.execute(upload_stmt)
                    ).scalar_one_or_none()
                    if upload is None:
                        raise TracecatValidationError(
                            f"Skill upload '{operation.upload_id}' not found",
                            detail={"code": "upload_not_found"},
                        )
                    path_to_blob[
                        normalized_path
                    ] = await self._materialize_uploaded_blob(upload)
                case SkillDraftDeleteFileOp():
                    normalized_path = self._normalize_path(operation.path)
                    path_to_blob.pop(normalized_path, None)

        await self._replace_draft_with_blob_map(skill=skill, path_to_blob=path_to_blob)
        await self.session.commit()
        return await self._build_draft_read(skill)

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_draft_upload(
        self, *, skill_id: uuid.UUID, params: SkillUploadSessionCreate
    ) -> SkillUploadSessionRead:
        """Create a staged upload session for a draft blob."""

        skill = await self.get_skill(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")

        upload_id = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(seconds=DEFAULT_UPLOAD_TTL_SECONDS)
        storage_key = self._staged_upload_key_for(
            upload_id=upload_id, sha256=params.sha256
        )
        upload_row = SkillUploadModel(
            workspace_id=self.workspace_id,
            skill_id=skill.id,
            sha256=params.sha256,
            size_bytes=params.size_bytes,
            content_type=params.content_type,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            key=storage_key,
            expires_at=expires_at,
            created_by=self.role.user_id if self.role.type == "user" else None,
        )
        upload_row.id = upload_id
        self.session.add(upload_row)
        await self.session.commit()
        return SkillUploadSessionRead(
            upload_id=upload_id,
            upload_url=await blob.generate_presigned_upload_url(
                key=storage_key,
                bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
                content_type=params.content_type,
                expiry=DEFAULT_UPLOAD_TTL_SECONDS,
            ),
            headers={"Content-Type": params.content_type},
            expires_at=expires_at,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            key=storage_key,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version(self, version_id: uuid.UUID) -> SkillVersion | None:
        """Return a skill version by ID."""

        stmt = select(SkillVersion).where(
            SkillVersion.workspace_id == self.workspace_id,
            SkillVersion.id == version_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def publish_skill(self, skill_id: uuid.UUID) -> SkillVersionRead:
        """Publish the current draft into a new immutable skill version."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        rows = await self._list_draft_rows(skill.id)
        validation = await self._validate_manifest_rows(
            [(draft_file.path, blob_row) for draft_file, blob_row in rows]
        )
        if validation.errors:
            raise TracecatValidationError(
                "Skill draft failed validation",
                detail={
                    "code": "skill_publish_validation_failed",
                    "errors": [
                        error.model_dump(mode="json") for error in validation.errors
                    ],
                },
            )

        manifest_payload = [
            {
                "path": draft_file.path,
                "sha256": blob_row.sha256,
                "size_bytes": blob_row.size_bytes,
                "content_type": blob_row.content_type,
            }
            for draft_file, blob_row in rows
        ]
        manifest_sha256 = self._compute_sha256(orjson.dumps(manifest_payload))

        stmt = (
            select(SkillVersion.version)
            .where(
                SkillVersion.workspace_id == self.workspace_id,
                SkillVersion.skill_id == skill.id,
            )
            .order_by(SkillVersion.version.desc())
            .limit(1)
        )
        current_version_number = (await self.session.execute(stmt)).scalar_one_or_none()
        next_version = (current_version_number or 0) + 1
        version = SkillVersion(
            workspace_id=self.workspace_id,
            skill_id=skill.id,
            version=next_version,
            manifest_sha256=manifest_sha256,
            file_count=len(rows),
            total_size_bytes=sum(blob_row.size_bytes for _, blob_row in rows),
            title=validation.title,
            description=validation.description,
        )
        self.session.add(version)
        await self.session.flush()
        for draft_file, _blob_row in rows:
            self.session.add(
                SkillVersionFile(
                    workspace_id=self.workspace_id,
                    skill_version_id=version.id,
                    path=draft_file.path,
                    blob_id=draft_file.blob_id,
                )
            )
        skill.current_version_id = version.id
        skill.title = validation.title
        skill.description = validation.description
        self.session.add(skill)
        await self.session.commit()
        return await self.get_version_read(skill_id=skill.id, version_id=version.id)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_versions(
        self, *, skill_id: uuid.UUID, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[SkillVersionRead]:
        """List immutable versions for a skill ordered newest first."""

        paginator = BaseCursorPaginator(self.session)
        stmt = select(SkillVersion).where(
            SkillVersion.workspace_id == self.workspace_id,
            SkillVersion.skill_id == skill_id,
        )
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError(
                    "Invalid cursor for skill versions"
                ) from err
            cursor_version = cursor_data.sort_value
            if not isinstance(cursor_version, int):
                raise TracecatValidationError("Invalid cursor for skill versions")
            predicate = sa.or_(
                SkillVersion.version < cursor_version,
                sa.and_(
                    SkillVersion.version == cursor_version, SkillVersion.id < cursor_id
                ),
            )
            if params.reverse:
                predicate = sa.or_(
                    SkillVersion.version > cursor_version,
                    sa.and_(
                        SkillVersion.version == cursor_version,
                        SkillVersion.id > cursor_id,
                    ),
                )
            stmt = stmt.where(predicate)

        if params.reverse:
            stmt = stmt.order_by(SkillVersion.version.asc(), SkillVersion.id.asc())
        else:
            stmt = stmt.order_by(SkillVersion.version.desc(), SkillVersion.id.desc())
        stmt = stmt.limit(params.limit + 1)
        versions = (await self.session.execute(stmt)).scalars().all()
        has_more = len(versions) > params.limit
        items = versions[: params.limit]

        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = paginator.encode_cursor(
                last.id,
                sort_column="version",
                sort_value=last.version,
            )

        prev_cursor = None
        if params.cursor and items:
            first = items[0]
            prev_cursor = paginator.encode_cursor(
                first.id,
                sort_column="version",
                sort_value=first.version,
            )

        return CursorPaginatedResponse(
            items=[
                await self.get_version_read(skill_id=skill_id, version_id=version.id)
                for version in items
            ],
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=params.cursor is not None,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version_read(
        self, *, skill_id: uuid.UUID, version_id: uuid.UUID
    ) -> SkillVersionRead:
        """Return a fully rendered published skill version."""

        version = await self.get_version(version_id)
        if version is None or version.skill_id != skill_id:
            raise TracecatNotFoundError(f"Skill version '{version_id}' not found")
        rows = await self._list_version_rows(version.id)
        return SkillVersionRead(
            id=version.id,
            skill_id=version.skill_id,
            workspace_id=version.workspace_id,
            version=version.version,
            manifest_sha256=version.manifest_sha256,
            file_count=version.file_count,
            total_size_bytes=version.total_size_bytes,
            title=version.title,
            description=version.description,
            created_at=version.created_at,
            updated_at=version.updated_at,
            files=[
                SkillFileEntry(
                    path=version_file.path,
                    blob_id=blob_row.id,
                    sha256=blob_row.sha256,
                    size_bytes=blob_row.size_bytes,
                    content_type=blob_row.content_type,
                )
                for version_file, blob_row in rows
            ],
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def restore_version(
        self, *, skill_id: uuid.UUID, version_id: uuid.UUID
    ) -> SkillDraftRead:
        """Replace the mutable draft with a published snapshot."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        version = await self.get_version(version_id)
        if version is None or version.skill_id != skill.id:
            raise TracecatNotFoundError(f"Skill version '{version_id}' not found")
        rows = await self._list_version_rows(version.id)
        await self._replace_draft_with_blob_map(
            skill=skill,
            path_to_blob={
                version_file.path: blob_row for version_file, blob_row in rows
            },
        )
        await self.session.commit()
        return await self._build_draft_read(skill)

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def archive_skill(self, skill_id: uuid.UUID) -> None:
        """Archive a skill unless it is still bound on a preset head."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        binding_stmt = (
            select(func.count())
            .select_from(AgentPresetSkill)
            .where(
                AgentPresetSkill.workspace_id == self.workspace_id,
                AgentPresetSkill.skill_id == skill.id,
            )
        )
        if int((await self.session.execute(binding_stmt)).scalar_one() or 0) > 0:
            raise TracecatValidationError(
                "Cannot archive a skill that is still attached to a preset",
                detail={"code": "skill_in_use"},
            )
        skill.archived_at = datetime.now(UTC)
        self.session.add(skill)
        await self.session.commit()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def validate_binding_inputs(
        self,
        bindings: Sequence[AgentPresetSkillBindingBase],
        *,
        for_update: bool = False,
    ) -> None:
        """Validate preset skill bindings before they are persisted."""

        if not bindings:
            return
        if len({binding.skill_id for binding in bindings}) != len(bindings):
            raise TracecatValidationError(
                "Duplicate skills are not allowed on a preset",
                detail={"code": "duplicate_skill_binding"},
            )

        skill_ids = [binding.skill_id for binding in bindings]
        skills = await self._get_bindable_skills(
            skill_ids,
            for_update=for_update,
        )
        missing = [str(skill_id) for skill_id in skill_ids if skill_id not in skills]
        if missing:
            raise TracecatValidationError(
                f"Some skills were not found in this workspace: {sorted(missing)}",
                detail={"code": "skill_not_found", "missing_skill_ids": missing},
            )

        for binding in bindings:
            skill = skills[binding.skill_id]
            if skill.current_version_id is None:
                raise TracecatValidationError(
                    f"Skill '{skill.slug}' has no published version",
                    detail={"code": "skill_not_published", "skill_id": str(skill.id)},
                )
            selected_version = await self.get_version(binding.skill_version_id)
            if selected_version is None or selected_version.skill_id != skill.id:
                raise TracecatValidationError(
                    "Selected skill version does not belong to the selected skill",
                    detail={"code": "invalid_skill_binding"},
                )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_resolved_skill_refs_for_preset_version(
        self, preset_version_id: uuid.UUID
    ) -> list[ResolvedSkillRef]:
        """Return exact skill refs for an immutable preset version."""

        stmt = (
            select(
                AgentPresetVersionSkill.skill_id,
                Skill.slug,
                AgentPresetVersionSkill.skill_version_id,
                SkillVersion.manifest_sha256,
            )
            .join(Skill, AgentPresetVersionSkill.skill_id == Skill.id)
            .join(
                SkillVersion,
                AgentPresetVersionSkill.skill_version_id == SkillVersion.id,
            )
            .where(
                AgentPresetVersionSkill.workspace_id == self.workspace_id,
                AgentPresetVersionSkill.preset_version_id == preset_version_id,
            )
            .order_by(Skill.slug.asc())
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        return [
            ResolvedSkillRef(
                skill_id=skill_id,
                skill_slug=skill_slug,
                skill_version_id=skill_version_id,
                manifest_sha256=manifest_sha256,
            )
            for skill_id, skill_slug, skill_version_id, manifest_sha256 in rows
        ]

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_resolved_skill_ref(
        self, *, skill_id: uuid.UUID, skill_version_id: uuid.UUID
    ) -> ResolvedSkillRef:
        """Return one exact skill ref for a published skill version."""

        stmt = (
            select(Skill.id, Skill.slug, SkillVersion.id, SkillVersion.manifest_sha256)
            .join(SkillVersion, SkillVersion.skill_id == Skill.id)
            .where(
                Skill.workspace_id == self.workspace_id,
                Skill.id == skill_id,
                SkillVersion.workspace_id == self.workspace_id,
                SkillVersion.id == skill_version_id,
            )
        )
        row = (await self.session.execute(stmt)).tuples().first()
        if row is None:
            raise TracecatNotFoundError(
                f"Skill version '{skill_version_id}' not found for skill '{skill_id}'"
            )
        resolved_skill_id, skill_slug, resolved_version_id, manifest_sha256 = row
        return ResolvedSkillRef(
            skill_id=resolved_skill_id,
            skill_slug=skill_slug,
            skill_version_id=resolved_version_id,
            manifest_sha256=manifest_sha256,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version_file_materialization(
        self, skill_version_id: uuid.UUID
    ) -> list[tuple[str, SkillBlob]]:
        """Return sorted published skill files for executor staging."""

        return [
            (version_file.path, blob_row)
            for version_file, blob_row in await self._list_version_rows(
                skill_version_id
            )
        ]
