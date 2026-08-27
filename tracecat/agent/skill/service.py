"""Service layer for workspace skills."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal, Never

import orjson
import sqlalchemy as sa
import yaml
from asyncpg import UniqueViolationError as AsyncpgUniqueViolationError
from psycopg.errors import UniqueViolation as PsycopgUniqueViolation
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat import config
from tracecat.agent.preset.schemas import AgentPresetSkillBindingBase
from tracecat.agent.skill.schemas import (
    NewSkillName,
    SkillCreate,
    SkillDownloadPreparedFile,
    SkillDownloadPreparedResponse,
    SkillDraftAttachUploadedBlobOp,
    SkillDraftDeleteFileOp,
    SkillDraftFileRead,
    SkillDraftMoveFileOp,
    SkillDraftPatch,
    SkillDraftRead,
    SkillDraftUpsertTextFileOp,
    SkillFileEntry,
    SkillName,
    SkillRead,
    SkillReadMinimal,
    SkillUpload,
    SkillUploadFile,
    SkillUploadSessionBatchRead,
    SkillUploadSessionCreate,
    SkillUploadSessionRead,
    SkillValidationErrorDetail,
    SkillVersionFileContent,
    SkillVersionPublish,
    SkillVersionRead,
    SkillVersionReadMinimal,
    SkillVersionSnapshotRead,
)
from tracecat.agent.skill.types import ResolvedSkillRef
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentPreset,
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
from tracecat.db.soft_delete import with_deleted
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.logger import logger
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
DEFAULT_DOWNLOAD_TTL_SECONDS = 15 * 60
MAX_CONTENT_TYPE_LENGTH = 255
SKILL_SLUG_MAX_LENGTH = 64
SKILL_SLUG_INSERT_ATTEMPTS = 3
SKILL_SLUG_UNIQUE_CONSTRAINT = "uq_skill_workspace_slug_active"
POSTGRES_UNIQUE_VIOLATION_SQLSTATE = "23505"
EXPIRED_UPLOAD_REAP_BATCH_SIZE = 64
# Lenient adapter for slug lookups: accepts legacy reserved-prefix identifiers.
SKILL_SLUG_ADAPTER = TypeAdapter(SkillName)
# Strict adapter for draft/publish manifest validation: rejects reserved names.
NEW_SKILL_NAME_ADAPTER = TypeAdapter(NewSkillName)


@dataclass(slots=True)
class ManifestValidationResult:
    """Result of validating a skill draft or published manifest."""

    name: str | None = None
    description: str | None = None
    errors: list[SkillValidationErrorDetail] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SkillFileBlobRef:
    """Presentation metadata for a skill file plus its blob row."""

    blob: SkillBlob
    content_type: str


@dataclass(frozen=True, slots=True)
class SkillFileSizeMetadata:
    """Path and declared byte size used for skill-wide limit checks."""

    path: str | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SkillFileLimitViolation:
    """One deterministic skill-tree limit violation."""

    code: str
    message: str
    path: str | None
    actual_field: str
    actual_value: int
    limit_field: str
    limit_value: int

    def exception_detail(self) -> dict[str, str | int]:
        """Return structured API error details for this violation."""

        detail: dict[str, str | int] = {
            "code": self.code,
            self.actual_field: self.actual_value,
            self.limit_field: self.limit_value,
        }
        if self.path is not None:
            detail["path"] = self.path
        return detail


@dataclass(frozen=True, slots=True)
class SkillBlobPublicationClaim:
    """Result of claiming one workspace-scoped blob identity for publication."""

    blob: SkillBlob
    is_owner: bool


@dataclass(frozen=True, slots=True)
class PublishedBlobObject:
    """Object written for a blob row that has not been committed yet."""

    bucket: str
    key: str


@dataclass(frozen=True, slots=True)
class StagedUploadObject:
    """Staged object whose owning database mutation has committed."""

    upload_id: uuid.UUID
    bucket: str
    key: str
    reason: Literal["reap_expired_upload", "upload_materialized"]


_staged_upload_cleanup_tasks: set[asyncio.Task[None]] = set()


async def _delete_staged_upload_objects(
    staged_objects: Sequence[StagedUploadObject],
) -> None:
    """Delete committed staged objects without retaining a service session."""

    for staged_object in staged_objects:
        try:
            await blob.delete_file(
                key=staged_object.key,
                bucket=staged_object.bucket,
                redact_log_identifiers=True,
            )
        except Exception as exc:
            logger.warning(
                "Failed to delete staged skill upload object",
                upload_id=str(staged_object.upload_id),
                reason=staged_object.reason,
                error_type=type(exc).__name__,
            )


def _finalize_staged_upload_cleanup(task: asyncio.Task[None]) -> None:
    """Release a completed cleanup task and surface unexpected failures."""

    _staged_upload_cleanup_tasks.discard(task)
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.error(
            "Expired skill upload cleanup task failed",
            error_type=type(exc).__name__,
        )


def _schedule_staged_upload_cleanup(
    staged_objects: Sequence[StagedUploadObject],
) -> None:
    """Schedule best-effort object deletion outside the response path."""

    if not staged_objects:
        return
    for stranded in [
        task for task in _staged_upload_cleanup_tasks if task.get_loop().is_closed()
    ]:
        _staged_upload_cleanup_tasks.discard(stranded)
    task = asyncio.create_task(_delete_staged_upload_objects(staged_objects))
    _staged_upload_cleanup_tasks.add(task)
    task.add_done_callback(_finalize_staged_upload_cleanup)


@dataclass(frozen=True, slots=True)
class PreparedSkillUploadFile:
    """Normalized one-shot upload file ready for validation and persistence."""

    path: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class PreparedSkillUploadDraft:
    """Validated one-shot upload files materialized as skill blob references."""

    validation: ManifestValidationResult
    path_to_blob: dict[str, SkillFileBlobRef]


@dataclass(frozen=True, slots=True)
class PreparedDraftTextFileOp:
    """Validated draft text-file upsert ready for blob materialization."""

    path: str
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class PreparedDraftAttachUploadedBlobOp:
    """Validated draft upload attachment ready for blob materialization."""

    path: str
    upload: SkillUploadModel


@dataclass(frozen=True, slots=True)
class PreparedDraftDeleteFileOp:
    """Validated draft file deletion."""

    path: str


@dataclass(frozen=True, slots=True)
class PreparedDraftMoveFileOp:
    """Validated draft file move (path rename, blob unchanged)."""

    from_path: str
    to_path: str


type PreparedDraftPatchOperation = (
    PreparedDraftTextFileOp
    | PreparedDraftAttachUploadedBlobOp
    | PreparedDraftDeleteFileOp
    | PreparedDraftMoveFileOp
)
type SkillDraftBlobMapFactory = Callable[
    [list[PublishedBlobObject]], Awaitable[dict[str, SkillFileBlobRef]]
]
type SkillBeforeCreateCommit = Callable[[Skill], Awaitable[None]]


def _integrity_error_sources(error: IntegrityError) -> Iterator[object]:
    """Yield every exception that may carry driver diagnostics for ``error``.

    The real driver exception can hide behind SQLAlchemy or adapter wrappers,
    so walk the ``__cause__``/``__context__`` chain of both the wrapper and
    its DBAPI ``orig`` payload (asyncpg surfaces the violation there).
    """

    seen: set[int] = set()

    def walk(exc: BaseException) -> Iterator[BaseException]:
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            yield current
            current = current.__cause__ or current.__context__

    yield from walk(error)
    orig = error.orig
    yield orig
    if isinstance(orig, BaseException):
        yield from walk(orig)


def _is_skill_slug_unique_violation(error: IntegrityError) -> bool:
    """True when ``error`` is a unique violation on the live-slug index.

    Detection is structural (repo rule: never match on error-message strings)
    and covers both drivers: a unique violation is the driver exception type
    or SQLSTATE 23505 (asyncpg ``sqlstate`` / psycopg ``pgcode``), and the
    violated constraint name comes from the exception itself (asyncpg) or its
    ``diag`` block (psycopg).
    """

    is_unique = False
    constraint_name: str | None = None
    for source in _integrity_error_sources(error):
        if isinstance(source, (AsyncpgUniqueViolationError, PsycopgUniqueViolation)):
            is_unique = True
        elif POSTGRES_UNIQUE_VIOLATION_SQLSTATE in (
            getattr(source, "sqlstate", None),
            getattr(source, "pgcode", None),
        ):
            is_unique = True
        if constraint_name is None:
            name = getattr(source, "constraint_name", None)
            if not isinstance(name, str):
                name = getattr(getattr(source, "diag", None), "constraint_name", None)
            if isinstance(name, str):
                constraint_name = name
    return is_unique and constraint_name == SKILL_SLUG_UNIQUE_CONSTRAINT


class SkillService(BaseWorkspaceService):
    """CRUD operations and execution helpers for workspace skills."""

    service_name = "skill"

    @staticmethod
    def _compute_sha256(content: bytes) -> str:
        """Return the SHA256 digest for a skill blob."""

        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _normalize_sha256(sha256: str) -> str:
        """Return a canonical lowercase SHA-256 hex digest."""

        return sha256.strip().lower()

    @staticmethod
    def _normalize_content_type(content_type: str) -> str:
        """Return a canonical MIME type string for skill file metadata."""

        parts = [part.strip().lower() for part in content_type.split(";")]
        normalized = "; ".join(part for part in parts if part)
        if not normalized:
            raise TracecatValidationError(
                "Skill file content type cannot be empty",
                detail={"code": "invalid_content_type", "reason": "empty"},
            )
        if len(normalized) > MAX_CONTENT_TYPE_LENGTH:
            raise TracecatValidationError(
                "Skill file content type must be 255 characters or fewer",
                detail={
                    "code": "invalid_content_type",
                    "reason": "too_long",
                    "max_length": MAX_CONTENT_TYPE_LENGTH,
                },
            )
        return normalized

    @staticmethod
    def _validate_skill_file_limits(
        files: Sequence[SkillFileSizeMetadata],
    ) -> None:
        """Enforce file-count and byte-size limits for a complete skill tree."""

        if violation := SkillService._skill_file_limit_violation(files):
            raise TracecatValidationError(
                violation.message,
                detail=violation.exception_detail(),
            )

    @staticmethod
    def _validate_skill_transfer_file_count(file_count: int) -> None:
        """Bound synchronous staged transfers before object-store work begins."""

        if file_count <= config.TRACECAT__MAX_SKILL_TRANSFER_FILES_COUNT:
            return
        violation = SkillFileLimitViolation(
            code="skill_transfer_file_count_limit_exceeded",
            message="Skill staged transfer contains too many files",
            path=None,
            actual_field="file_count",
            actual_value=file_count,
            limit_field="max_file_count",
            limit_value=config.TRACECAT__MAX_SKILL_TRANSFER_FILES_COUNT,
        )
        raise TracecatValidationError(
            violation.message,
            detail=violation.exception_detail(),
        )

    @staticmethod
    def _skill_file_limit_violation(
        files: Sequence[SkillFileSizeMetadata],
    ) -> SkillFileLimitViolation | None:
        """Return the first deterministic skill-tree limit violation."""

        if len(files) > config.TRACECAT__MAX_SKILL_FILES_COUNT:
            return SkillFileLimitViolation(
                code="skill_file_count_limit_exceeded",
                message="Skill draft contains too many files",
                path=None,
                actual_field="file_count",
                actual_value=len(files),
                limit_field="max_file_count",
                limit_value=config.TRACECAT__MAX_SKILL_FILES_COUNT,
            )

        manifest = next((file for file in files if file.path == "SKILL.md"), None)
        if (
            manifest is not None
            and manifest.size_bytes > config.TRACECAT__MAX_SKILL_MANIFEST_SIZE_BYTES
        ):
            return SkillFileLimitViolation(
                code="skill_manifest_size_limit_exceeded",
                message="Root SKILL.md exceeds the size limit",
                path="SKILL.md",
                actual_field="size_bytes",
                actual_value=manifest.size_bytes,
                limit_field="max_size_bytes",
                limit_value=config.TRACECAT__MAX_SKILL_MANIFEST_SIZE_BYTES,
            )

        oversized_file = max(
            (
                file
                for file in files
                if file.size_bytes > config.TRACECAT__MAX_SKILL_FILE_SIZE_BYTES
            ),
            key=lambda file: (file.size_bytes, file.path or ""),
            default=None,
        )
        if oversized_file is not None:
            return SkillFileLimitViolation(
                code="skill_file_size_limit_exceeded",
                message="Skill file exceeds the size limit",
                path=oversized_file.path,
                actual_field="size_bytes",
                actual_value=oversized_file.size_bytes,
                limit_field="max_size_bytes",
                limit_value=config.TRACECAT__MAX_SKILL_FILE_SIZE_BYTES,
            )

        total_size_bytes = sum(file.size_bytes for file in files)

        if total_size_bytes > config.TRACECAT__MAX_SKILL_TOTAL_SIZE_BYTES:
            return SkillFileLimitViolation(
                code="skill_total_size_limit_exceeded",
                message="Skill draft exceeds the aggregate size limit",
                path=None,
                actual_field="total_size_bytes",
                actual_value=total_size_bytes,
                limit_field="max_total_size_bytes",
                limit_value=config.TRACECAT__MAX_SKILL_TOTAL_SIZE_BYTES,
            )
        return None

    def _validate_skill_blob_map_limits(
        self, path_to_blob: dict[str, SkillFileBlobRef]
    ) -> None:
        """Enforce skill limits against the materialized draft file map."""

        self._validate_skill_file_limits(
            [
                SkillFileSizeMetadata(
                    path=path,
                    size_bytes=file_ref.blob.size_bytes,
                )
                for path, file_ref in path_to_blob.items()
            ]
        )

    def _storage_key_for(self, sha256: str) -> str:
        """Return the canonical storage key for a skill blob."""

        normalized_sha256 = self._normalize_sha256(sha256)
        return f"skills/{self.workspace_id}/{normalized_sha256}"

    def _staged_upload_key_for(self, *, upload_id: uuid.UUID, sha256: str) -> str:
        """Return the temporary storage key for a staged skill upload."""

        normalized_sha256 = self._normalize_sha256(sha256)
        return f"skill-uploads/{self.workspace_id}/{upload_id}/{normalized_sha256}"

    def _staged_upload_prefix(self) -> str:
        """Return the storage-prefix used for staged upload objects."""

        return f"skill-uploads/{self.workspace_id}/"

    def _legacy_staged_upload_prefix(self) -> str:
        """Return the pre-lifecycle staged prefix for cleanup compatibility."""

        return f"skills/{self.workspace_id}/uploads/"

    def _is_staged_upload_object(self, upload: SkillUploadModel) -> bool:
        """Return whether an upload still points at a temporary staged object."""

        return (
            upload.completed_at is None
            and upload.blob_id is None
            and upload.key.startswith(
                (self._staged_upload_prefix(), self._legacy_staged_upload_prefix())
            )
        )

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
    def _normalize_skill_markdown_for_parsing(skill_markdown: str) -> str:
        """Normalize markdown before delimiter-based parsing."""

        return (
            skill_markdown.removeprefix("\ufeff")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    @staticmethod
    def _split_skill_markdown_frontmatter(
        skill_markdown: str,
    ) -> tuple[str, str] | None:
        """Split normalized root SKILL.md frontmatter from its body."""

        if not skill_markdown.startswith("---\n"):
            return None
        _, _, remainder = skill_markdown.partition("---\n")
        frontmatter, separator, body = remainder.partition("\n---\n")
        if separator:
            return frontmatter, body
        closing_delimiter = "\n---"
        if remainder.endswith(closing_delimiter):
            return remainder[: -len(closing_delimiter)], ""
        return None

    @staticmethod
    def _build_default_skill_markdown(*, name: str, description: str | None) -> str:
        """Create the seeded root SKILL.md for a new skill."""

        metadata: dict[str, str] = {"name": name}
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
                f"# {name}",
                "",
                "Describe when this skill should be used and what it does.",
            ]
        )

    async def _build_default_draft_blob_map(
        self,
        *,
        name: str,
        description: str | None,
        published: list[PublishedBlobObject] | None = None,
    ) -> dict[str, SkillFileBlobRef]:
        """Materialize the seeded root manifest for a new skill."""

        root_markdown = self._build_default_skill_markdown(
            name=name,
            description=description,
        )
        root_blob = await self._get_or_create_blob(
            content=root_markdown.encode("utf-8"),
            published=published,
        )
        return {
            "SKILL.md": SkillFileBlobRef(
                blob=root_blob,
                content_type="text/markdown; charset=utf-8",
            )
        }

    @staticmethod
    def _merge_skill_markdown_metadata(
        skill_markdown: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> str:
        """Merge name/description frontmatter into an existing SKILL.md body."""

        skill_markdown = SkillService._normalize_skill_markdown_for_parsing(
            skill_markdown
        )
        metadata: dict[str, object] = {}
        body = skill_markdown

        if frontmatter_parts := SkillService._split_skill_markdown_frontmatter(
            skill_markdown
        ):
            frontmatter, body = frontmatter_parts
            try:
                loaded = yaml.safe_load(frontmatter) or {}
            except yaml.YAMLError:
                loaded = {}
            if isinstance(loaded, dict):
                metadata = dict(loaded)

        if name is not None:
            metadata["name"] = name
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
    def _raise_missing_draft_name(*, operation: str) -> Never:
        """Raise a validation error when a draft is missing a required manifest name."""

        raise TracecatValidationError(
            f"Skill draft is missing a required name during {operation}",
            detail={"code": "missing_skill_name", "operation": operation},
        )

    @staticmethod
    def _raise_missing_version_name(*, skill_version_id: uuid.UUID) -> Never:
        """Raise a validation error when a published skill version is malformed."""

        raise TracecatValidationError(
            f"Skill version '{skill_version_id}' is missing a required name",
            detail={
                "code": "missing_skill_version_name",
                "skill_version_id": str(skill_version_id),
            },
        )

    @staticmethod
    def _extract_frontmatter(skill_markdown: str) -> tuple[str | None, str | None]:
        """Extract name and description from root SKILL.md frontmatter.

        Raises:
            TracecatValidationError: If the frontmatter contains invalid YAML.
        """

        skill_markdown = SkillService._normalize_skill_markdown_for_parsing(
            skill_markdown
        )
        frontmatter_parts = SkillService._split_skill_markdown_frontmatter(
            skill_markdown
        )
        if frontmatter_parts is None:
            return None, None
        frontmatter, _ = frontmatter_parts
        try:
            loaded = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError as exc:
            raise TracecatValidationError(
                "Root SKILL.md frontmatter must be valid YAML",
                detail={"code": "invalid_skill_md_frontmatter", "path": "SKILL.md"},
            ) from exc
        if not isinstance(loaded, dict):
            return None, None
        name = loaded.get("name")
        description = loaded.get("description")
        return (
            name if isinstance(name, str) and name.strip() else None,
            description
            if isinstance(description, str) and description.strip()
            else None,
        )

    async def _get_blob_by_identity(self, *, sha256: str) -> SkillBlob | None:
        """Return the blob row for a workspace-scoped content identity."""

        normalized_sha256 = self._normalize_sha256(sha256)
        stmt = select(SkillBlob).where(
            SkillBlob.workspace_id == self.workspace_id,
            SkillBlob.sha256 == normalized_sha256,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _claim_blob_publication(
        self,
        *,
        sha256: str,
        bucket: str,
        key: str,
        size_bytes: int,
    ) -> SkillBlobPublicationClaim:
        """Claim a digest for publication, or reuse the concurrent winner.

        The existing workspace/digest uniqueness constraint arbitrates only
        writers for the same content identity. The row stays uncommitted until
        its owner finishes publishing and verifying the canonical object.
        """

        normalized_sha256 = self._normalize_sha256(sha256)
        stmt = (
            pg_insert(SkillBlob)
            .values(
                workspace_id=self.workspace_id,
                sha256=normalized_sha256,
                bucket=bucket,
                key=key,
                size_bytes=size_bytes,
            )
            .on_conflict_do_nothing(constraint="uq_skill_blob_workspace_sha256")
            .returning(SkillBlob.id)
        )
        blob_id = (await self.session.execute(stmt)).scalar_one_or_none()
        if blob_id is not None:
            blob_row = await self.get_blob(blob_id)
            if blob_row is None:
                raise TracecatNotFoundError(f"Skill blob '{blob_id}' not found")
            return SkillBlobPublicationClaim(blob=blob_row, is_owner=True)

        existing = await self._get_blob_by_identity(sha256=normalized_sha256)
        if existing is None:
            raise TracecatNotFoundError(
                "Skill blob row was not found after a concurrent insert"
            )
        return SkillBlobPublicationClaim(blob=existing, is_owner=False)

    async def _get_or_create_blob(
        self,
        *,
        content: bytes,
        published: list[PublishedBlobObject] | None = None,
    ) -> SkillBlob:
        """Create or reuse a content-addressed skill blob.

        Args:
            content: Blob payload.
            published: Collector for objects this call writes for a new,
                still-uncommitted blob row, so a caller that rolls back can
                delete them. Reused rows are never recorded.

        Returns:
            The deduplicated blob row.
        """

        sha256 = self._compute_sha256(content)
        existing = await self._get_blob_by_identity(sha256=sha256)
        if existing is not None:
            return existing

        storage_key = self._storage_key_for(sha256)
        claim = await self._claim_blob_publication(
            sha256=sha256,
            bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
            key=storage_key,
            size_bytes=len(content),
        )
        if not claim.is_owner:
            return claim.blob

        # Record the prospective key before the network call: a CancelledError
        # can arrive after object storage accepts the upload but before the
        # client receives its response. Rollback cleanup may therefore issue a
        # harmless no-op delete when the upload did not create an object.
        if published is not None:
            published.append(
                PublishedBlobObject(
                    bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
                    key=storage_key,
                )
            )
        try:
            await blob.upload_file(
                content=content,
                key=storage_key,
                bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
                content_type="application/octet-stream",
            )
        except Exception:
            await self.session.delete(claim.blob)
            raise
        return claim.blob

    async def _delete_published_blob_objects_best_effort(
        self, published: Sequence[PublishedBlobObject]
    ) -> None:
        """Delete objects written for blob rows that are about to roll back.

        Call this before the SQL rollback. The uncommitted rows still hold the
        workspace/digest uniqueness claim, so no concurrent writer can have
        published the same key yet and the delete cannot remove another
        transaction's object. The deletes are shielded so a cancellation
        arriving mid-cleanup cannot abort it partway.
        """

        if not published:
            return
        await asyncio.shield(self._delete_blob_objects(published))

    async def _delete_blob_objects(
        self, published: Sequence[PublishedBlobObject]
    ) -> None:
        """Delete each object, logging instead of raising on failure."""

        for obj in published:
            try:
                await blob.delete_file(
                    key=obj.key,
                    bucket=obj.bucket,
                    redact_log_identifiers=True,
                )
            except Exception as exc:
                self.logger.warning(
                    "Failed to delete rolled-back skill blob object",
                    error_type=type(exc).__name__,
                )

    async def _stream_verify_object(
        self,
        *,
        key: str,
        bucket: str,
        expected_sha256: str,
        expected_size_bytes: int,
        error_detail: dict[str, str],
    ) -> None:
        """Stream a stored object and require an exact size and SHA-256 match."""

        actual_size_bytes = 0
        hasher = hashlib.sha256()
        async with blob.open_download_stream(key=key, bucket=bucket) as (
            stream,
            content_length,
        ):
            if content_length is not None and content_length > expected_size_bytes:
                raise TracecatValidationError(
                    "Uploaded blob size mismatch",
                    detail=error_detail,
                )
            async for chunk in stream.iter_chunks(
                chunk_size=blob.DEFAULT_DOWNLOAD_CHUNK_SIZE_BYTES
            ):
                if not chunk:
                    continue
                actual_size_bytes += len(chunk)
                if actual_size_bytes > expected_size_bytes:
                    raise TracecatValidationError(
                        "Uploaded blob size mismatch",
                        detail=error_detail,
                    )
                hasher.update(chunk)
        if hasher.hexdigest() != expected_sha256:
            raise TracecatValidationError(
                "Uploaded blob SHA-256 mismatch",
                detail=error_detail,
            )
        if actual_size_bytes != expected_size_bytes:
            raise TracecatValidationError(
                "Uploaded blob size mismatch",
                detail=error_detail,
            )

    async def _materialize_uploaded_blob(
        self,
        upload: SkillUploadModel,
        *,
        published: list[PublishedBlobObject] | None = None,
    ) -> SkillBlob:
        """Finalize a staged upload into a reusable blob row.

        Args:
            upload: Staged upload session row.
            published: Collector for the canonical object this call copies for
                a new, still-uncommitted blob row. Reused rows and uploads that
                already sit at their canonical key are never recorded.
        """

        if upload.completed_at is not None and upload.blob_id is not None:
            blob_row = await self.get_blob(upload.blob_id)
            if blob_row is None:
                raise TracecatNotFoundError(f"Skill blob '{upload.blob_id}' not found")
            return blob_row

        if upload.expires_at < datetime.now(UTC):
            if self._is_staged_upload_object(upload):
                await self._delete_staged_upload_object_best_effort(
                    upload,
                    reason="upload_expired",
                )
            raise TracecatValidationError(
                "Skill upload session has expired",
                detail={"code": "upload_expired", "upload_id": str(upload.id)},
            )
        if not await blob.file_exists(key=upload.key, bucket=upload.bucket):
            raise TracecatValidationError(
                "Uploaded blob was not found in object storage",
                detail={"code": "upload_missing", "upload_id": str(upload.id)},
            )

        integrity_error_detail = {
            "code": "upload_integrity_error",
            "upload_id": str(upload.id),
        }
        normalized_upload_sha256 = self._normalize_sha256(upload.sha256)
        await self._stream_verify_object(
            key=upload.key,
            bucket=upload.bucket,
            expected_sha256=normalized_upload_sha256,
            expected_size_bytes=upload.size_bytes,
            error_detail=integrity_error_detail,
        )

        blob_row = await self._get_blob_by_identity(sha256=normalized_upload_sha256)
        if blob_row is None:
            canonical_key = self._storage_key_for(normalized_upload_sha256)
            claim = await self._claim_blob_publication(
                sha256=normalized_upload_sha256,
                bucket=upload.bucket,
                key=canonical_key,
                size_bytes=upload.size_bytes,
            )
            blob_row = claim.blob
            if claim.is_owner and upload.key != canonical_key:
                # Record the prospective key before the network call: a
                # CancelledError can arrive after object storage accepts the
                # copy but before the client receives its response. Rollback
                # cleanup may therefore issue a harmless no-op delete when the
                # copy did not create an object.
                if published is not None:
                    published.append(
                        PublishedBlobObject(bucket=upload.bucket, key=canonical_key)
                    )
                try:
                    await blob.copy_file(
                        source_key=upload.key,
                        destination_key=canonical_key,
                        bucket=upload.bucket,
                        content_type="application/octet-stream",
                    )
                    # The staged PUT URL may still be valid here, so a concurrent
                    # re-PUT between the verification above and the copy could
                    # poison the content-addressed blob. Verify the canonical copy
                    # itself before it becomes reusable.
                    await self._stream_verify_object(
                        key=canonical_key,
                        bucket=upload.bucket,
                        expected_sha256=normalized_upload_sha256,
                        expected_size_bytes=upload.size_bytes,
                        error_detail=integrity_error_detail,
                    )
                except Exception:
                    await self.session.delete(claim.blob)
                    try:
                        await blob.delete_file(
                            key=canonical_key,
                            bucket=upload.bucket,
                            redact_log_identifiers=True,
                        )
                    except Exception as exc:
                        self.logger.warning(
                            "Failed to delete unverified canonical blob object",
                            error_type=type(exc).__name__,
                        )
                    raise

        upload.blob_id = blob_row.id
        upload.completed_at = datetime.now(UTC)
        self.session.add(upload)
        await self.session.flush()
        return blob_row

    async def _delete_staged_upload_object_best_effort(
        self,
        upload: SkillUploadModel,
        *,
        reason: str,
    ) -> None:
        """Delete a temporary staged upload object without failing the caller."""

        try:
            await blob.delete_file(
                key=upload.key,
                bucket=upload.bucket,
                redact_log_identifiers=True,
            )
        except Exception as exc:
            self.logger.warning(
                "Failed to delete staged skill upload object",
                upload_id=str(upload.id),
                reason=reason,
                error_type=type(exc).__name__,
            )

    async def _reap_expired_incomplete_uploads(self) -> list[StagedUploadObject]:
        """Delete expired incomplete upload rows and return staged objects to clean up."""

        expired_stmt = (
            select(SkillUploadModel)
            .where(
                SkillUploadModel.workspace_id == self.workspace_id,
                SkillUploadModel.completed_at.is_(None),
                SkillUploadModel.expires_at < datetime.now(UTC),
            )
            .order_by(SkillUploadModel.expires_at.asc(), SkillUploadModel.id.asc())
            .limit(EXPIRED_UPLOAD_REAP_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        expired_uploads = (await self.session.execute(expired_stmt)).scalars().all()
        if not expired_uploads:
            return []

        reaped_objects = [
            StagedUploadObject(
                upload_id=upload.id,
                bucket=upload.bucket,
                key=upload.key,
                reason="reap_expired_upload",
            )
            for upload in expired_uploads
            if self._is_staged_upload_object(upload)
        ]
        for upload in expired_uploads:
            await self.session.delete(upload)
        await self.session.flush()
        return reaped_objects

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
        file_sizes: list[SkillFileSizeMetadata] = []

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
            file_sizes.append(
                SkillFileSizeMetadata(path=normalized, size_bytes=blob_row.size_bytes)
            )
            if normalized == "SKILL.md":
                skill_md_blob = blob_row

        if violation := self._skill_file_limit_violation(file_sizes):
            result.errors.append(
                SkillValidationErrorDetail(
                    code=violation.code,
                    message=violation.message,
                    path=violation.path,
                )
            )
            if skill_md_blob is not None and skill_md_blob.size_bytes > min(
                config.TRACECAT__MAX_SKILL_MANIFEST_SIZE_BYTES,
                config.TRACECAT__MAX_SKILL_FILE_SIZE_BYTES,
            ):
                return result

        for normalized in sorted(seen_paths):
            parts = normalized.split("/")
            for index in range(1, len(parts)):
                ancestor = "/".join(parts[:index])
                if ancestor in seen_paths:
                    result.errors.append(
                        SkillValidationErrorDetail(
                            code="path_prefix_collision",
                            message=(
                                f"Skill path {normalized!r} conflicts with file path "
                                f"{ancestor!r}"
                            ),
                            path=normalized,
                        )
                    )
                    break

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
            result.name, result.description = self._extract_frontmatter(markdown)
        except TracecatValidationError as exc:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="invalid_skill_md_frontmatter",
                    message=str(exc),
                    path="SKILL.md",
                )
            )
            return result
        if result.name is None:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="missing_skill_name",
                    message="Root SKILL.md frontmatter must define a skill name",
                    path="SKILL.md",
                )
            )
            return result
        try:
            result.name = NEW_SKILL_NAME_ADAPTER.validate_python(result.name)
        except ValidationError:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="invalid_skill_name",
                    message=(
                        "Root SKILL.md frontmatter name must be 1-64 characters "
                        "of lowercase letters, numbers, and single hyphens, and "
                        "must not use the reserved 'tracecat-' prefix"
                    ),
                    path="SKILL.md",
                )
            )
        return result

    def _validate_prepared_upload_files(
        self, files: Sequence[PreparedSkillUploadFile]
    ) -> ManifestValidationResult:
        """Validate normalized one-shot upload files before blob writes."""

        result = ManifestValidationResult()
        seen_paths: set[str] = set()
        skill_md_content: bytes | None = None

        for file in files:
            if file.path in seen_paths:
                result.errors.append(
                    SkillValidationErrorDetail(
                        code="duplicate_path",
                        message=f"Duplicate skill path {file.path!r}",
                        path=file.path,
                    )
                )
            seen_paths.add(file.path)
            if file.path == "SKILL.md":
                skill_md_content = file.content

        for normalized in sorted(seen_paths):
            parts = normalized.split("/")
            for index in range(1, len(parts)):
                ancestor = "/".join(parts[:index])
                if ancestor in seen_paths:
                    result.errors.append(
                        SkillValidationErrorDetail(
                            code="path_prefix_collision",
                            message=(
                                f"Skill path {normalized!r} conflicts with file path "
                                f"{ancestor!r}"
                            ),
                            path=normalized,
                        )
                    )
                    break

        if skill_md_content is None:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="missing_root_skill_md",
                    message="Root SKILL.md is required",
                    path="SKILL.md",
                )
            )
            return result

        try:
            markdown = skill_md_content.decode("utf-8")
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
            result.name, result.description = self._extract_frontmatter(markdown)
        except TracecatValidationError as exc:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="invalid_skill_md_frontmatter",
                    message=str(exc),
                    path="SKILL.md",
                )
            )
            return result
        if result.name is None:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="missing_skill_name",
                    message="Root SKILL.md frontmatter must define a skill name",
                    path="SKILL.md",
                )
            )
            return result
        try:
            result.name = NEW_SKILL_NAME_ADAPTER.validate_python(result.name)
        except ValidationError:
            result.errors.append(
                SkillValidationErrorDetail(
                    code="invalid_skill_name",
                    message=(
                        "Root SKILL.md frontmatter name must be 1-64 characters "
                        "of lowercase letters, numbers, and single hyphens, and "
                        "must not use the reserved 'tracecat-' prefix"
                    ),
                    path="SKILL.md",
                )
            )
        return result

    def _prepare_upload_files(
        self, files: Sequence[SkillUploadFile]
    ) -> list[PreparedSkillUploadFile]:
        """Normalize and decode file payloads before validation."""

        prepared_files: list[PreparedSkillUploadFile] = []
        for file_payload in files:
            path = self._normalize_path(file_payload.path)
            try:
                content = base64.b64decode(file_payload.content_base64, validate=True)
            except ValueError as exc:
                raise TracecatValidationError(
                    f"Invalid base64 content for skill path {path!r}",
                    detail={"code": "invalid_base64", "path": path},
                ) from exc
            content_type = self._normalize_content_type(
                file_payload.content_type or self._guess_content_type(path)
            )
            prepared_files.append(
                PreparedSkillUploadFile(
                    path=path,
                    content=content,
                    content_type=content_type,
                )
            )
        self._validate_skill_file_limits(
            [
                SkillFileSizeMetadata(path=file.path, size_bytes=len(file.content))
                for file in prepared_files
            ]
        )
        return prepared_files

    async def _prepare_validated_upload_draft(
        self, params: SkillUpload
    ) -> PreparedSkillUploadDraft:
        """Validate and materialize a one-shot upload into draft blob refs."""

        validation, prepared_files = self._validate_upload_draft(params)
        return await self._materialize_upload_draft(
            validation=validation,
            prepared_files=prepared_files,
        )

    def _validate_upload_draft(
        self, params: SkillUpload
    ) -> tuple[ManifestValidationResult, list[PreparedSkillUploadFile]]:
        """Validate a one-shot upload without writing blob rows."""

        prepared_files = self._prepare_upload_files(params.files)
        validation = self._validate_prepared_upload_files(prepared_files)
        if validation.errors:
            raise TracecatValidationError(
                "Uploaded skill draft failed validation",
                detail={
                    "code": "skill_upload_validation_failed",
                    "errors": [
                        error.model_dump(mode="json") for error in validation.errors
                    ],
                },
            )
        if validation.name != params.name:
            raise TracecatValidationError(
                "Uploaded skill name must match the root SKILL.md frontmatter name",
                detail={
                    "code": "skill_name_mismatch",
                    "expected_name": params.name,
                    "actual_name": validation.name,
                },
            )
        return validation, prepared_files

    async def _materialize_upload_draft(
        self,
        *,
        validation: ManifestValidationResult,
        prepared_files: Sequence[PreparedSkillUploadFile],
        published: list[PublishedBlobObject] | None = None,
    ) -> PreparedSkillUploadDraft:
        """Materialize validated upload files into draft blob refs."""

        path_to_blob = await self._materialize_prepared_files(
            prepared_files, published=published
        )
        return PreparedSkillUploadDraft(
            validation=validation,
            path_to_blob=path_to_blob,
        )

    async def _materialize_prepared_files(
        self,
        prepared_files: Sequence[PreparedSkillUploadFile],
        *,
        published: list[PublishedBlobObject] | None = None,
    ) -> dict[str, SkillFileBlobRef]:
        """Materialize file blobs in stable digest order to avoid lock cycles."""

        path_to_blob: dict[str, SkillFileBlobRef] = {}
        for file in sorted(
            prepared_files,
            key=lambda item: (self._compute_sha256(item.content), item.path),
        ):
            path_to_blob[file.path] = SkillFileBlobRef(
                blob=await self._get_or_create_blob(
                    content=file.content, published=published
                ),
                content_type=file.content_type,
            )
        return path_to_blob

    async def _create_version_from_blob_refs(
        self,
        *,
        skill: Skill,
        file_refs: Sequence[tuple[str, SkillFileBlobRef]],
        validation: ManifestValidationResult,
    ) -> SkillVersionRead:
        """Create a new immutable version from validated skill files."""

        if validation.name is None:
            self._raise_missing_draft_name(operation="publish")
        manifest_name = validation.name
        sorted_file_refs = sorted(file_refs, key=lambda item: item[0])
        manifest_payload = [
            {
                "path": path,
                "sha256": file_ref.blob.sha256,
                "size_bytes": file_ref.blob.size_bytes,
                "content_type": file_ref.content_type,
            }
            for path, file_ref in sorted_file_refs
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
            file_count=len(sorted_file_refs),
            total_size_bytes=sum(
                file_ref.blob.size_bytes for _, file_ref in sorted_file_refs
            ),
            name=manifest_name,
            description=validation.description,
        )
        self.session.add(version)
        await self.session.flush()
        for path, file_ref in sorted_file_refs:
            self.session.add(
                SkillVersionFile(
                    workspace_id=self.workspace_id,
                    skill_version_id=version.id,
                    path=path,
                    blob_id=file_ref.blob.id,
                    content_type=file_ref.content_type,
                )
            )
        skill.current_version_id = version.id
        skill.name = manifest_name
        skill.description = validation.description
        self.session.add(skill)
        await self.session.commit()
        return await self.get_version_read(skill_id=skill.id, version_id=version.id)

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
                    content_type=draft_file.content_type,
                )
            )
            validation_pairs.append((draft_file.path, blob_row))
        validation = await self._validate_manifest_rows(validation_pairs)
        return SkillDraftRead(
            skill_id=skill.id,
            skill_name=skill.name,
            draft_revision=skill.draft_revision,
            name=validation.name,
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
            current_version_summary = SkillVersionReadMinimal(
                id=current_version.id,
                skill_id=current_version.skill_id,
                workspace_id=current_version.workspace_id,
                version=current_version.version,
                manifest_sha256=current_version.manifest_sha256,
                file_count=current_version.file_count,
                total_size_bytes=current_version.total_size_bytes,
                name=current_version.name,
                description=current_version.description,
                created_at=current_version.created_at,
                updated_at=current_version.updated_at,
            )
        return SkillRead(
            id=skill.id,
            workspace_id=skill.workspace_id,
            name=skill.name,
            # Legacy writers (old pods during the expand window) insert rows
            # without a slug; surface the backfill semantics (slug := name)
            # until the contract release backfills and sets NOT NULL.
            slug=skill.slug or skill.name,
            description=skill.description,
            current_version_id=skill.current_version_id,
            draft_revision=skill.draft_revision,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
            deleted_at=skill.deleted_at or skill.archived_at,
            current_version=current_version_summary,
            is_draft_publishable=draft.is_publishable,
            draft_validation_errors=draft.validation_errors,
            draft_file_count=len(draft.files),
        )

    @staticmethod
    def _build_skill_read_minimal(skill: Skill) -> SkillReadMinimal:
        """Build the minimal list response for a skill."""

        return SkillReadMinimal(
            id=skill.id,
            workspace_id=skill.workspace_id,
            name=skill.name,
            # Same expand-window projection as SkillRead: legacy rows inserted
            # without a slug present their name as the slug.
            slug=skill.slug or skill.name,
            description=skill.description,
            current_version_id=skill.current_version_id,
            created_at=skill.created_at,
            updated_at=skill.updated_at,
            deleted_at=skill.deleted_at or skill.archived_at,
        )

    async def _validate_skill_slug_available(self, slug: str) -> None:
        """Ensure a live skill slug is not already reserved in this workspace.

        Used by workspace-sync import, where the manifest names an exact slug
        and silently suffixing would diverge from the commit. Interactive
        creation uses ``_allocate_skill_slug`` instead.
        """

        if await self._skill_slug_exists(slug):
            raise TracecatValidationError(
                f"Skill slug '{slug}' is already in use for this workspace",
                detail={"code": "skill_slug_conflict", "slug": slug},
            )

    async def _allocate_skill_slug(self, desired: str) -> str:
        """Return a live-unique slug for this workspace.

        Duplicate skill names remain allowed (names were never unique); the
        slug carries live-row uniqueness instead. Collisions get the same
        deterministic ``-2``/``-3`` suffixes the slug backfill migration uses.
        Rare concurrent races are caught by the partial unique index.
        """

        if not await self._skill_slug_exists(desired):
            return desired
        counter = 2
        while True:
            candidate = self._suffixed_skill_slug(desired, counter)
            if not await self._skill_slug_exists(candidate):
                return candidate
            counter += 1

    @staticmethod
    def _suffixed_skill_slug(slug: str, counter: int) -> str:
        suffix = f"-{counter}"
        return f"{slug[: SKILL_SLUG_MAX_LENGTH - len(suffix)]}{suffix}"

    async def _skill_slug_exists(self, slug: str) -> bool:
        """Check whether a live row already occupies ``slug`` in this workspace.

        Legacy rows inserted by old pods during the expand window have
        ``slug IS NULL`` and project their name as the slug (see
        ``_build_skill_read``), so they reserve their name too. The contract
        backfill assigns them that slug permanently.

        Liveness matches the ``uq_skill_workspace_slug_active`` partial-index
        predicate (both columns NULL): legacy pods archive by setting only
        ``archived_at``, and such effectively-dead rows must not report a slug
        as taken that the index would accept.
        """

        stmt = select(
            sa.exists().where(
                Skill.workspace_id == self.workspace_id,
                sa.or_(
                    Skill.slug == slug,
                    sa.and_(Skill.slug.is_(None), Skill.name == slug),
                ),
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
        )
        return bool((await self.session.execute(stmt)).scalar_one())

    async def _create_skill_with_slug_retry(
        self,
        *,
        name: str,
        description: str | None,
        path_to_blob_factory: SkillDraftBlobMapFactory,
        before_commit: SkillBeforeCreateCommit | None = None,
    ) -> Skill:
        """Create a skill and draft, then atomically run optional preparation."""

        for _ in range(SKILL_SLUG_INSERT_ATTEMPTS):
            slug = await self._allocate_skill_slug(name)
            skill = Skill(
                workspace_id=self.workspace_id,
                name=name,
                slug=slug,
                draft_revision=0,
                description=description,
            )
            self.session.add(skill)
            # Objects the factory writes for new blob rows live outside the
            # SQL transaction, so a rollback has to delete them explicitly.
            # A failed commit is ambiguous (the rows may have landed), so its
            # objects are left in place rather than risk orphaning a row.
            published: list[PublishedBlobObject] = []
            committing = False
            try:
                await self.session.flush()
                await self._replace_draft_with_blob_map(
                    skill=skill,
                    path_to_blob=await path_to_blob_factory(published),
                )
                if before_commit is not None:
                    await before_commit(skill)
                committing = True
                await self.session.commit()
            except IntegrityError as exc:
                if not committing:
                    await self._delete_published_blob_objects_best_effort(published)
                await self.session.rollback()
                if not _is_skill_slug_unique_violation(exc):
                    raise
            except BaseException:
                # BaseException so a CancelledError from the MCP tool timeout
                # still deletes the published objects before rolling back.
                if not committing:
                    await self._delete_published_blob_objects_best_effort(published)
                await self.session.rollback()
                raise
            else:
                await self.session.refresh(skill)
                return skill

        raise TracecatValidationError(
            "Could not allocate a unique skill slug after concurrent writes",
            detail={"code": "skill_slug_conflict", "name": name},
        )

    async def _replace_draft_with_blob_map(
        self, *, skill: Skill, path_to_blob: dict[str, SkillFileBlobRef]
    ) -> None:
        """Replace the draft manifest with a new set of blob references."""

        await self.session.execute(
            sa.delete(SkillDraftFile).where(
                SkillDraftFile.workspace_id == self.workspace_id,
                SkillDraftFile.skill_id == skill.id,
            )
        )
        for path, file_ref in sorted(path_to_blob.items()):
            self.session.add(
                SkillDraftFile(
                    workspace_id=self.workspace_id,
                    skill_id=skill.id,
                    path=path,
                    blob_id=file_ref.blob.id,
                    content_type=file_ref.content_type,
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
    async def get_skill(
        self, skill_id: uuid.UUID, *, include_archived: bool = False
    ) -> Skill | None:
        """Return a skill by ID."""

        predicates = [
            Skill.workspace_id == self.workspace_id,
            Skill.id == skill_id,
        ]
        if not include_archived:
            # Expand-window check: legacy writers set only archived_at; the
            # contract release drops the archived_at leg.
            predicates.extend((Skill.deleted_at.is_(None), Skill.archived_at.is_(None)))
        stmt = (
            select(Skill)
            .options(selectinload(Skill.current_version))
            .where(*predicates)
        )
        if include_archived:
            stmt = with_deleted(stmt)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_skill_by_identifier(
        self, identifier: str | uuid.UUID
    ) -> Skill | None:
        """Return a skill by UUID first, then by live slug.

        UUID objects and UUID-shaped strings resolve by ``Skill.id`` first. If a
        UUID-shaped string does not match an ID, the live skill whose slug equals
        that string remains reachable. Non-UUID strings resolve by live slug.
        """

        if isinstance(identifier, uuid.UUID):
            return await self.get_skill(identifier)
        try:
            parsed_skill_id = uuid.UUID(identifier)
        except ValueError:
            parsed_skill_id = None

        if parsed_skill_id is not None:
            if uuid_match := await self.get_skill(parsed_skill_id):
                return uuid_match

        try:
            skill_slug = SKILL_SLUG_ADAPTER.validate_python(identifier)
        except ValidationError:
            return None
        # Legacy rows inserted by old pods during the expand window have
        # ``slug IS NULL`` and project their name as the slug (see
        # ``_build_skill_read``), so they must stay reachable by that slug.
        # If both an exact-slug row and a legacy row match, the exact slug
        # wins; ties order like the backfill migration (created_at, id).
        stmt = (
            select(Skill)
            .options(selectinload(Skill.current_version))
            .where(
                Skill.workspace_id == self.workspace_id,
                sa.or_(
                    Skill.slug == skill_slug,
                    sa.and_(Skill.slug.is_(None), Skill.name == skill_slug),
                ),
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            .order_by(
                sa.case((Skill.slug == skill_slug, 0), else_=1),
                Skill.created_at.asc(),
                Skill.id.asc(),
            )
            .limit(2)
        )
        skills = (await self.session.execute(stmt)).scalars().all()
        if not skills:
            return None
        if skills[0].slug == skill_slug:
            # Exact slug matches are unique among live rows
            # (``uq_skill_workspace_slug_active``), so this is deterministic.
            return skills[0]
        # Only legacy (``slug IS NULL``) rows matched. The partial unique index
        # does not constrain NULL slugs and old pods enforced no name
        # uniqueness, so multiple live rows can project the same fallback slug.
        # Binding one silently would be a silent substitution; fail loud. This
        # edge self-extinguishes at contract when slugs are backfilled NOT NULL.
        if len(skills) > 1:
            raise TracecatValidationError(
                "Skill identifier is ambiguous",
                detail={"code": "ambiguous_skill_id"},
            ) from None
        return skills[0]

    async def _get_active_version(
        self, *, skill_id: uuid.UUID, version_id: uuid.UUID
    ) -> SkillVersion | None:
        """Return a published version only when its parent skill is active."""

        stmt = (
            select(SkillVersion)
            .join(
                Skill,
                sa.and_(
                    Skill.id == SkillVersion.skill_id,
                    Skill.workspace_id == SkillVersion.workspace_id,
                ),
            )
            .where(
                SkillVersion.workspace_id == self.workspace_id,
                SkillVersion.id == version_id,
                SkillVersion.skill_id == skill_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_skill_for_update(
        self, skill_id: uuid.UUID, *, include_archived: bool = False
    ) -> Skill | None:
        """Return and lock a skill row for mutation."""

        predicates = [
            Skill.workspace_id == self.workspace_id,
            Skill.id == skill_id,
        ]
        if not include_archived:
            predicates.extend((Skill.deleted_at.is_(None), Skill.archived_at.is_(None)))
        stmt = select(Skill).where(*predicates).with_for_update()
        if include_archived:
            stmt = with_deleted(stmt)
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
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            return {
                skill.id: skill
                for skill in (await self.session.execute(stmt)).scalars().all()
            }

        stmt = (
            select(Skill)
            .where(
                Skill.workspace_id == self.workspace_id,
                Skill.id.in_(normalized_ids),
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            .order_by(Skill.id)
            .with_for_update()
        )
        return {
            skill.id: skill
            for skill in (await self.session.execute(stmt)).scalars().all()
        }

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_skill(self, params: SkillCreate) -> SkillRead:
        """Create a logical skill and seed its initial draft.

        Args:
            params: Skill creation payload.

        Returns:
            The created skill summary.
        """

        async def default_draft_blob_map(
            published: list[PublishedBlobObject],
        ) -> dict[str, SkillFileBlobRef]:
            return await self._build_default_draft_blob_map(
                name=params.name,
                description=params.description,
                published=published,
            )

        skill = await self._create_skill_with_slug_retry(
            name=params.name,
            description=params.description,
            path_to_blob_factory=default_draft_blob_map,
        )
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

        validation, prepared_files = self._validate_upload_draft(params)

        async def uploaded_draft_blob_map(
            published: list[PublishedBlobObject],
        ) -> dict[str, SkillFileBlobRef]:
            prepared_draft = await self._materialize_upload_draft(
                validation=validation,
                prepared_files=prepared_files,
                published=published,
            )
            return prepared_draft.path_to_blob

        skill = await self._create_skill_with_slug_retry(
            name=params.name,
            description=validation.description,
            path_to_blob_factory=uploaded_draft_blob_map,
        )
        return await self._build_skill_read(skill)

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def replace_skill_draft(
        self, *, skill_id: uuid.UUID, params: SkillUpload
    ) -> SkillRead:
        """Replace an existing skill's mutable draft with a full file tree."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")

        prepared_draft = await self._prepare_validated_upload_draft(params)
        await self._replace_draft_with_blob_map(
            skill=skill,
            path_to_blob=prepared_draft.path_to_blob,
        )
        if skill.current_version_id is None:
            skill.name = params.name
            skill.description = prepared_draft.validation.description
            self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return await self._build_skill_read(skill)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_skills(
        self, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[SkillReadMinimal]:
        """List workspace skills with cursor pagination."""

        paginator = BaseCursorPaginator(self.session)
        stmt = select(Skill).where(
            Skill.workspace_id == self.workspace_id,
            Skill.deleted_at.is_(None),
            Skill.archived_at.is_(None),
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
            items=[self._build_skill_read_minimal(skill) for skill in items],
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
    async def prepare_draft_download(
        self,
        *,
        skill_id: uuid.UUID,
        url_expiry_seconds: int = DEFAULT_DOWNLOAD_TTL_SECONDS,
    ) -> SkillDownloadPreparedResponse | None:
        """Prepare presigned downloads for every file in a skill draft."""

        if (skill := await self.get_skill(skill_id)) is None:
            return None

        rows = await self._list_draft_rows(skill_id)
        self._validate_skill_file_limits(
            [
                SkillFileSizeMetadata(
                    path=draft_file.path,
                    size_bytes=blob_row.size_bytes,
                )
                for draft_file, blob_row in rows
            ]
        )
        # A bulk download is one synchronous transfer, so it is bounded by the
        # same per-transfer file cap as uploads and completions.
        self._validate_skill_transfer_file_count(len(rows))
        expires_at = datetime.now(UTC) + timedelta(seconds=url_expiry_seconds)
        files = [
            SkillDownloadPreparedFile(
                path=draft_file.path,
                sha256=blob_row.sha256,
                size_bytes=blob_row.size_bytes,
                content_type=draft_file.content_type,
                download_url=await blob.generate_presigned_download_url(
                    key=blob_row.key,
                    bucket=blob_row.bucket,
                    override_content_type=draft_file.content_type,
                    expiry=url_expiry_seconds,
                ),
                expires_at=expires_at,
            )
            for draft_file, blob_row in rows
        ]
        return SkillDownloadPreparedResponse(
            workspace_id=self.workspace_id,
            skill_id=skill.id,
            skill_name=skill.name,
            draft_revision=skill.draft_revision,
            files=files,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_draft_file(
        self,
        *,
        skill_id: uuid.UUID,
        path: str,
        url_expiry_seconds: int = DEFAULT_DOWNLOAD_TTL_SECONDS,
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
        draft_file, blob_row = row
        self._validate_skill_file_limits(
            [
                SkillFileSizeMetadata(
                    path=draft_file.path,
                    size_bytes=blob_row.size_bytes,
                )
            ]
        )
        if self._is_inline_text(
            draft_file.content_type, size_bytes=blob_row.size_bytes
        ):
            try:
                content = await blob.download_file(
                    key=blob_row.key,
                    bucket=blob_row.bucket,
                )
                return SkillDraftFileRead(
                    kind="inline",
                    path=normalized_path,
                    content_type=draft_file.content_type,
                    size_bytes=blob_row.size_bytes,
                    sha256=blob_row.sha256,
                    text_content=content.decode("utf-8"),
                )
            except UnicodeDecodeError:
                pass

        return SkillDraftFileRead(
            kind="download",
            path=normalized_path,
            content_type=draft_file.content_type,
            size_bytes=blob_row.size_bytes,
            sha256=blob_row.sha256,
            download_url=await blob.generate_presigned_download_url(
                key=blob_row.key,
                bucket=blob_row.bucket,
                override_content_type=draft_file.content_type,
                expiry=url_expiry_seconds,
            ),
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version_file(
        self, *, skill_id: uuid.UUID, version_id: uuid.UUID, path: str
    ) -> SkillDraftFileRead | None:
        """Return one published version file inline or as a presigned download."""

        normalized_path = self._normalize_path(path)
        stmt = (
            select(SkillVersionFile, SkillBlob)
            .join(SkillBlob, SkillVersionFile.blob_id == SkillBlob.id)
            .where(
                SkillVersionFile.workspace_id == self.workspace_id,
                SkillVersionFile.skill_version_id == version_id,
                SkillVersionFile.path == normalized_path,
            )
        )
        row = (await self.session.execute(stmt)).tuples().first()
        if row is None:
            return None
        version_file, blob_row = row

        version = await self._get_active_version(
            skill_id=skill_id, version_id=version_id
        )
        if version is None:
            return None

        if self._is_inline_text(
            version_file.content_type, size_bytes=blob_row.size_bytes
        ):
            try:
                content = await blob.download_file(
                    key=blob_row.key,
                    bucket=blob_row.bucket,
                )
                return SkillDraftFileRead(
                    kind="inline",
                    path=normalized_path,
                    content_type=version_file.content_type,
                    size_bytes=blob_row.size_bytes,
                    sha256=blob_row.sha256,
                    text_content=content.decode("utf-8"),
                )
            except UnicodeDecodeError:
                pass

        return SkillDraftFileRead(
            kind="download",
            path=normalized_path,
            content_type=version_file.content_type,
            size_bytes=blob_row.size_bytes,
            sha256=blob_row.sha256,
            download_url=await blob.generate_presigned_download_url(
                key=blob_row.key,
                bucket=blob_row.bucket,
                override_content_type=version_file.content_type,
                expiry=DEFAULT_DOWNLOAD_TTL_SECONDS,
            ),
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_draft_text_file(
        self, *, skill_id: uuid.UUID, path: str
    ) -> str | None:
        """Return one draft file decoded as UTF-8 text regardless of inline size."""

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
        try:
            content = await blob.download_file(
                key=blob_row.key,
                bucket=blob_row.bucket,
            )
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return None

    async def _prepare_draft_patch_operations(
        self,
        *,
        skill: Skill,
        operations: Sequence[
            SkillDraftUpsertTextFileOp
            | SkillDraftAttachUploadedBlobOp
            | SkillDraftDeleteFileOp
            | SkillDraftMoveFileOp
        ],
    ) -> list[PreparedDraftPatchOperation]:
        """Validate draft operations before any blob writes begin."""

        transfer_file_count = sum(
            isinstance(operation, SkillDraftAttachUploadedBlobOp)
            for operation in operations
        )
        self._validate_skill_transfer_file_count(transfer_file_count)
        upload_ids = {
            operation.upload_id
            for operation in operations
            if isinstance(operation, SkillDraftAttachUploadedBlobOp)
        }
        uploads_by_id: dict[uuid.UUID, SkillUploadModel] = {}
        if upload_ids:
            upload_stmt = select(SkillUploadModel).where(
                SkillUploadModel.workspace_id == self.workspace_id,
                SkillUploadModel.skill_id == skill.id,
                SkillUploadModel.id.in_(upload_ids),
            )
            uploads = (await self.session.execute(upload_stmt)).scalars().all()
            uploads_by_id = {upload.id: upload for upload in uploads}

        prepared_operations: list[PreparedDraftPatchOperation] = []
        for operation in operations:
            match operation:
                case SkillDraftUpsertTextFileOp():
                    prepared_operations.append(
                        PreparedDraftTextFileOp(
                            path=self._normalize_path(operation.path),
                            content=operation.content.encode("utf-8"),
                            content_type=self._normalize_content_type(
                                operation.content_type
                            ),
                        )
                    )
                case SkillDraftAttachUploadedBlobOp():
                    normalized_path = self._normalize_path(operation.path)
                    upload = uploads_by_id.get(operation.upload_id)
                    if upload is None:
                        raise TracecatValidationError(
                            f"Skill upload '{operation.upload_id}' not found",
                            detail={"code": "upload_not_found"},
                        )
                    prepared_operations.append(
                        PreparedDraftAttachUploadedBlobOp(
                            path=normalized_path,
                            upload=upload,
                        )
                    )
                case SkillDraftDeleteFileOp():
                    prepared_operations.append(
                        PreparedDraftDeleteFileOp(
                            path=self._normalize_path(operation.path),
                        )
                    )
                case SkillDraftMoveFileOp():
                    from_path = self._normalize_path(operation.from_path)
                    to_path = self._normalize_path(operation.to_path)
                    if from_path == to_path:
                        raise TracecatValidationError(
                            "Move source and destination must differ",
                            detail={
                                "code": "invalid_move",
                                "from_path": from_path,
                                "to_path": to_path,
                            },
                        )
                    if from_path == "SKILL.md":
                        raise TracecatValidationError(
                            "Root SKILL.md cannot be moved",
                            detail={
                                "code": "skill_md_immovable",
                                "from_path": from_path,
                            },
                        )
                    if to_path == "SKILL.md":
                        raise TracecatValidationError(
                            "Cannot overwrite root SKILL.md via move",
                            detail={
                                "code": "skill_md_immovable",
                                "to_path": to_path,
                            },
                        )
                    prepared_operations.append(
                        PreparedDraftMoveFileOp(
                            from_path=from_path,
                            to_path=to_path,
                        )
                    )
        return prepared_operations

    async def _materialize_patch_operation_blobs(
        self,
        operations: Sequence[PreparedDraftPatchOperation],
        *,
        published: list[PublishedBlobObject] | None = None,
    ) -> dict[int, SkillBlob]:
        """Materialize every new blob in deterministic digest order."""

        pending: list[tuple[str, int, PreparedDraftPatchOperation]] = []
        for index, operation in enumerate(operations):
            match operation:
                case PreparedDraftTextFileOp():
                    pending.append(
                        (self._compute_sha256(operation.content), index, operation)
                    )
                case PreparedDraftAttachUploadedBlobOp():
                    pending.append(
                        (
                            self._normalize_sha256(operation.upload.sha256),
                            index,
                            operation,
                        )
                    )
                case PreparedDraftDeleteFileOp() | PreparedDraftMoveFileOp():
                    continue

        materialized: dict[int, SkillBlob] = {}
        for _, index, operation in sorted(pending, key=lambda item: (item[0], item[1])):
            match operation:
                case PreparedDraftTextFileOp():
                    materialized[index] = await self._get_or_create_blob(
                        content=operation.content, published=published
                    )
                case PreparedDraftAttachUploadedBlobOp():
                    materialized[index] = await self._materialize_uploaded_blob(
                        operation.upload, published=published
                    )
                case PreparedDraftDeleteFileOp() | PreparedDraftMoveFileOp():
                    raise AssertionError("non-materialized operation was queued")
        return materialized

    def _validate_patch_tree_before_materialization(
        self,
        *,
        current_rows: Sequence[tuple[SkillDraftFile, SkillBlob]],
        operations: Sequence[PreparedDraftPatchOperation],
    ) -> None:
        """Validate final paths and declared sizes before touching object storage."""

        path_to_size = {
            draft_file.path: blob_row.size_bytes
            for draft_file, blob_row in current_rows
        }
        for operation in operations:
            match operation:
                case PreparedDraftTextFileOp():
                    path_to_size[operation.path] = len(operation.content)
                case PreparedDraftAttachUploadedBlobOp():
                    path_to_size[operation.path] = operation.upload.size_bytes
                case PreparedDraftDeleteFileOp():
                    path_to_size.pop(operation.path, None)
                case PreparedDraftMoveFileOp():
                    source_size = path_to_size.get(operation.from_path)
                    if source_size is None:
                        raise TracecatValidationError(
                            f"Cannot move missing draft file '{operation.from_path}'",
                            detail={
                                "code": "move_source_not_found",
                                "from_path": operation.from_path,
                            },
                        )
                    if operation.to_path in path_to_size:
                        raise TracecatValidationError(
                            f"Move target '{operation.to_path}' already exists",
                            detail={
                                "code": "move_target_exists",
                                "to_path": operation.to_path,
                            },
                        )
                    path_to_size[operation.to_path] = source_size
                    path_to_size.pop(operation.from_path)

        self._validate_skill_file_limits(
            [
                SkillFileSizeMetadata(path=path, size_bytes=size_bytes)
                for path, size_bytes in path_to_size.items()
            ]
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

        prepared_operations = await self._prepare_draft_patch_operations(
            skill=skill,
            operations=params.operations,
        )
        current_rows = await self._list_draft_rows(skill.id)
        self._validate_patch_tree_before_materialization(
            current_rows=current_rows,
            operations=prepared_operations,
        )
        path_to_blob = {
            draft_file.path: SkillFileBlobRef(
                blob=blob_row,
                content_type=draft_file.content_type,
            )
            for draft_file, blob_row in current_rows
        }
        staged_upload_objects_to_delete = {
            StagedUploadObject(
                upload_id=operation.upload.id,
                key=operation.upload.key,
                bucket=operation.upload.bucket,
                reason="upload_materialized",
            )
            for operation in prepared_operations
            if isinstance(operation, PreparedDraftAttachUploadedBlobOp)
            and operation.upload.completed_at is None
            and operation.upload.key != self._storage_key_for(operation.upload.sha256)
        }
        # Canonical objects materialized here live outside the SQL transaction,
        # so a failure on a later operation has to delete the ones already
        # written, before the rollback releases their digest claims. A failed
        # commit is ambiguous (the rows may have landed), so its objects stay.
        published: list[PublishedBlobObject] = []
        committing = False
        try:
            materialized_blobs = await self._materialize_patch_operation_blobs(
                prepared_operations, published=published
            )
            for index, operation in enumerate(prepared_operations):
                match operation:
                    case PreparedDraftTextFileOp():
                        path_to_blob[operation.path] = SkillFileBlobRef(
                            blob=materialized_blobs[index],
                            content_type=operation.content_type,
                        )
                    case PreparedDraftAttachUploadedBlobOp():
                        path_to_blob[operation.path] = SkillFileBlobRef(
                            blob=materialized_blobs[index],
                            content_type=operation.upload.content_type,
                        )
                    case PreparedDraftDeleteFileOp():
                        path_to_blob.pop(operation.path, None)
                    case PreparedDraftMoveFileOp():
                        source = path_to_blob.pop(operation.from_path)
                        path_to_blob[operation.to_path] = source

            self._validate_skill_blob_map_limits(path_to_blob)
            validation = await self._validate_manifest_rows(
                [(path, file_ref.blob) for path, file_ref in path_to_blob.items()]
            )
            await self._replace_draft_with_blob_map(
                skill=skill, path_to_blob=path_to_blob
            )
            if (
                skill.current_version_id is None
                and not validation.errors
                and validation.name is not None
            ):
                skill.name = validation.name
                skill.description = validation.description
                self.session.add(skill)
            committing = True
            await self.session.commit()
        except BaseException:
            # BaseException so a CancelledError from the MCP tool timeout
            # still deletes the published objects before rolling back.
            if not committing:
                await self._delete_published_blob_objects_best_effort(published)
            await self.session.rollback()
            raise
        _schedule_staged_upload_cleanup(tuple(staged_upload_objects_to_delete))
        return await self._build_draft_read(skill)

    def _validate_upload_session_batch(
        self, params: Sequence[SkillUploadSessionCreate]
    ) -> None:
        """Validate a complete upload-session batch before database writes."""

        if not params:
            raise TracecatValidationError(
                "Skill upload must include at least one file",
                detail={"code": "skill_upload_empty"},
            )
        self._validate_skill_transfer_file_count(len(params))
        self._validate_skill_file_limits(
            [
                SkillFileSizeMetadata(path=None, size_bytes=upload.size_bytes)
                for upload in params
            ]
        )

    async def _prepare_draft_upload_rows(
        self,
        *,
        skill: Skill,
        params: Sequence[SkillUploadSessionCreate],
        url_expiry_seconds: int,
    ) -> list[SkillUploadSessionRead]:
        """Create and sign upload rows without committing their transaction."""

        if url_expiry_seconds <= 0:
            raise TracecatValidationError(
                "Skill upload URL expiry must be positive",
                detail={"code": "invalid_upload_url_expiry"},
            )
        self._validate_upload_session_batch(params)

        expires_at = datetime.now(UTC) + timedelta(seconds=url_expiry_seconds)
        prepared_rows: list[tuple[SkillUploadModel, str]] = []
        for upload_params in params:
            upload_id = uuid.uuid4()
            normalized_sha256 = self._normalize_sha256(upload_params.sha256)
            storage_key = self._staged_upload_key_for(
                upload_id=upload_id,
                sha256=normalized_sha256,
            )
            normalized_content_type = self._normalize_content_type(
                upload_params.content_type
            )
            upload_row = SkillUploadModel(
                workspace_id=self.workspace_id,
                skill_id=skill.id,
                sha256=normalized_sha256,
                size_bytes=upload_params.size_bytes,
                content_type=normalized_content_type,
                bucket=config.TRACECAT__BLOB_STORAGE_BUCKET_SKILLS,
                key=storage_key,
                expires_at=expires_at,
                created_by=self.role.user_id if self.role.type == "user" else None,
            )
            upload_row.id = upload_id
            self.session.add(upload_row)
            prepared_rows.append((upload_row, normalized_content_type))
        await self.session.flush()

        uploads: list[SkillUploadSessionRead] = []
        for upload_row, content_type in prepared_rows:
            checksum_sha256 = base64.b64encode(bytes.fromhex(upload_row.sha256)).decode(
                "ascii"
            )
            uploads.append(
                SkillUploadSessionRead(
                    upload_id=upload_row.id,
                    upload_url=await blob.generate_presigned_upload_url(
                        key=upload_row.key,
                        bucket=upload_row.bucket,
                        content_type=content_type,
                        checksum_sha256=checksum_sha256,
                        expiry=url_expiry_seconds,
                    ),
                    headers={
                        "Content-Type": content_type,
                        "Content-Length": str(upload_row.size_bytes),
                        "x-amz-checksum-sha256": checksum_sha256,
                    },
                    expires_at=expires_at,
                    bucket=upload_row.bucket,
                    key=upload_row.key,
                )
            )
        return uploads

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def prepare_draft_uploads(
        self,
        *,
        skill_id: uuid.UUID,
        params: Sequence[SkillUploadSessionCreate],
        url_expiry_seconds: int = DEFAULT_UPLOAD_TTL_SECONDS,
    ) -> SkillUploadSessionBatchRead:
        """Atomically prepare a complete upload batch for an existing skill."""

        self._validate_upload_session_batch(params)
        skill = await self.get_skill(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        expired_uploads = await self._reap_expired_incomplete_uploads()
        try:
            uploads = await self._prepare_draft_upload_rows(
                skill=skill,
                params=params,
                url_expiry_seconds=url_expiry_seconds,
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        prepared = SkillUploadSessionBatchRead(
            skill_id=skill.id,
            draft_revision=skill.draft_revision,
            created=False,
            uploads=uploads,
        )
        _schedule_staged_upload_cleanup(expired_uploads)
        return prepared

    @require_scope("agent:create", "agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def prepare_new_skill_draft_uploads(
        self,
        *,
        skill_params: SkillCreate,
        params: Sequence[SkillUploadSessionCreate],
        url_expiry_seconds: int = DEFAULT_UPLOAD_TTL_SECONDS,
    ) -> SkillUploadSessionBatchRead:
        """Atomically create a skill and prepare its complete upload batch."""

        self._validate_upload_session_batch(params)
        prepared_uploads: list[SkillUploadSessionRead] = []
        expired_uploads: list[StagedUploadObject] = []

        async def default_draft_blob_map(
            published: list[PublishedBlobObject],
        ) -> dict[str, SkillFileBlobRef]:
            return await self._build_default_draft_blob_map(
                name=skill_params.name,
                description=skill_params.description,
                published=published,
            )

        async def prepare_before_commit(skill: Skill) -> None:
            nonlocal expired_uploads, prepared_uploads
            expired_uploads = await self._reap_expired_incomplete_uploads()
            prepared_uploads = await self._prepare_draft_upload_rows(
                skill=skill,
                params=params,
                url_expiry_seconds=url_expiry_seconds,
            )

        skill = await self._create_skill_with_slug_retry(
            name=skill_params.name,
            description=skill_params.description,
            path_to_blob_factory=default_draft_blob_map,
            before_commit=prepare_before_commit,
        )
        prepared = SkillUploadSessionBatchRead(
            skill_id=skill.id,
            draft_revision=skill.draft_revision,
            created=True,
            uploads=prepared_uploads,
        )
        _schedule_staged_upload_cleanup(expired_uploads)
        return prepared

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_draft_upload(
        self,
        *,
        skill_id: uuid.UUID,
        params: SkillUploadSessionCreate,
    ) -> SkillUploadSessionRead:
        """Create one staged upload session for the draft-upload REST API."""

        prepared = await self.prepare_draft_uploads(
            skill_id=skill_id,
            params=[params],
        )
        return prepared.uploads[0]

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
        self._validate_skill_file_limits(
            [
                SkillFileSizeMetadata(
                    path=draft_file.path,
                    size_bytes=blob_row.size_bytes,
                )
                for draft_file, blob_row in rows
            ]
        )
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
        return await self._create_version_from_blob_refs(
            skill=skill,
            file_refs=[
                (
                    draft_file.path,
                    SkillFileBlobRef(
                        blob=blob_row,
                        content_type=draft_file.content_type,
                    ),
                )
                for draft_file, blob_row in rows
            ],
            validation=validation,
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def publish_skill_version(
        self, *, skill_id: uuid.UUID, params: SkillVersionPublish
    ) -> SkillVersionRead:
        """Atomically publish a new immutable skill version from a file set."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        if skill.current_version_id != params.base_version_id:
            raise TracecatValidationError(
                "Skill version conflict",
                detail={
                    "code": "skill_version_conflict",
                    "current_version_id": (
                        str(skill.current_version_id)
                        if skill.current_version_id is not None
                        else None
                    ),
                },
            )

        prepared_files = self._prepare_upload_files(params.files)
        validation = self._validate_prepared_upload_files(prepared_files)
        if validation.errors:
            raise TracecatValidationError(
                "Skill version failed validation",
                detail={
                    "code": "skill_version_validation_failed",
                    "errors": [
                        error.model_dump(mode="json") for error in validation.errors
                    ],
                },
            )
        file_refs = list(
            (await self._materialize_prepared_files(prepared_files)).items()
        )
        return await self._create_version_from_blob_refs(
            skill=skill,
            file_refs=file_refs,
            validation=validation,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_versions(
        self, *, skill_id: uuid.UUID, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[SkillVersionReadMinimal]:
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
                SkillVersionReadMinimal(
                    id=version.id,
                    skill_id=version.skill_id,
                    workspace_id=version.workspace_id,
                    version=version.version,
                    manifest_sha256=version.manifest_sha256,
                    file_count=version.file_count,
                    total_size_bytes=version.total_size_bytes,
                    name=version.name,
                    description=version.description,
                    created_at=version.created_at,
                    updated_at=version.updated_at,
                )
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

        version = await self._get_active_version(
            skill_id=skill_id, version_id=version_id
        )
        if version is None:
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
            name=version.name,
            description=version.description,
            created_at=version.created_at,
            updated_at=version.updated_at,
            files=[
                SkillFileEntry(
                    path=version_file.path,
                    blob_id=blob_row.id,
                    sha256=blob_row.sha256,
                    size_bytes=blob_row.size_bytes,
                    content_type=version_file.content_type,
                )
                for version_file, blob_row in rows
            ],
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version_snapshot_read(
        self, *, skill_id: uuid.UUID, version_id: uuid.UUID
    ) -> SkillVersionSnapshotRead:
        """Return a published skill version with publish-compatible file contents."""

        version = await self._get_active_version(
            skill_id=skill_id, version_id=version_id
        )
        if version is None:
            raise TracecatNotFoundError(f"Skill version '{version_id}' not found")
        rows = await self._list_version_rows(version.id)
        files: list[SkillVersionFileContent] = []
        for version_file, blob_row in rows:
            content = await blob.download_file(key=blob_row.key, bucket=blob_row.bucket)
            files.append(
                SkillVersionFileContent(
                    path=version_file.path,
                    content_base64=base64.b64encode(content).decode("ascii"),
                    content_type=version_file.content_type,
                    sha256=blob_row.sha256,
                    size_bytes=blob_row.size_bytes,
                    blob_id=blob_row.id,
                )
            )
        return SkillVersionSnapshotRead(
            id=version.id,
            skill_id=version.skill_id,
            workspace_id=version.workspace_id,
            version=version.version,
            manifest_sha256=version.manifest_sha256,
            file_count=version.file_count,
            total_size_bytes=version.total_size_bytes,
            name=version.name,
            description=version.description,
            created_at=version.created_at,
            updated_at=version.updated_at,
            files=files,
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def restore_version(
        self, *, skill_id: uuid.UUID, version_id: uuid.UUID
    ) -> SkillReadMinimal:
        """Restore a historical version as the current selected skill version."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        version = await self.get_version(version_id)
        if version is None or version.skill_id != skill.id:
            raise TracecatNotFoundError(f"Skill version '{version_id}' not found")
        if version.name is None:
            self._raise_missing_version_name(skill_version_id=version.id)
        skill.current_version_id = version.id
        skill.name = version.name
        skill.description = version.description
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return self._build_skill_read_minimal(skill)

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def archive_skill(self, skill_id: uuid.UUID) -> None:
        """Archive a skill unless any preset head still references it."""

        skill = await self._get_skill_for_update(skill_id)
        if skill is None:
            raise TracecatNotFoundError(f"Skill '{skill_id}' not found")
        binding_stmt = (
            select(func.count())
            .select_from(AgentPresetSkill)
            .join(
                AgentPreset,
                AgentPreset.id == AgentPresetSkill.preset_id,
            )
            .where(
                AgentPresetSkill.workspace_id == self.workspace_id,
                AgentPresetSkill.skill_id == skill.id,
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.deleted_at.is_(None),
            )
        )
        binding_count = int(
            (await self.session.execute(binding_stmt)).scalar_one() or 0
        )
        if binding_count > 0:
            raise TracecatValidationError(
                "Cannot delete a skill that is still referenced by a preset",
                detail={"code": "skill_in_use"},
            )
        archived_at = datetime.now(UTC)
        skill.archived_at = archived_at
        skill.deleted_at = archived_at
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
                    f"Skill '{skill.name}' has no published version",
                    detail={"code": "skill_not_published", "skill_id": str(skill.id)},
                )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_resolved_skill_refs_for_preset_version(
        self,
        preset_version_id: uuid.UUID,
        *,
        use_latest_versions: bool = False,
    ) -> list[ResolvedSkillRef]:
        """Return skill refs for an immutable preset version."""

        if use_latest_versions:
            return await self._get_latest_skill_refs_for_preset_version(
                preset_version_id
            )

        stmt = (
            select(
                AgentPresetVersionSkill.skill_id,
                SkillVersion.name,
                AgentPresetVersionSkill.skill_version_id,
                SkillVersion.manifest_sha256,
                Skill.deleted_at,
                Skill.archived_at,
            )
            .join(
                SkillVersion,
                AgentPresetVersionSkill.skill_version_id == SkillVersion.id,
            )
            .join(
                Skill,
                sa.and_(
                    AgentPresetVersionSkill.workspace_id == Skill.workspace_id,
                    AgentPresetVersionSkill.skill_id == Skill.id,
                ),
            )
            .where(
                AgentPresetVersionSkill.workspace_id == self.workspace_id,
                AgentPresetVersionSkill.preset_version_id == preset_version_id,
            )
            .order_by(SkillVersion.name.asc(), AgentPresetVersionSkill.skill_id.asc())
        )
        rows = (await self.session.execute(with_deleted(stmt))).tuples().all()
        resolved: list[ResolvedSkillRef] = []
        archived_skills: list[str] = []
        for (
            skill_id,
            skill_name,
            skill_version_id,
            manifest_sha256,
            deleted_at,
            archived_at,
        ) in rows:
            if skill_name is None:
                continue
            if deleted_at is not None or archived_at is not None:
                archived_skills.append(f"{skill_name} ({skill_id})")
                continue
            resolved.append(
                ResolvedSkillRef(
                    skill_id=skill_id,
                    skill_name=skill_name,
                    skill_version_id=skill_version_id,
                    manifest_sha256=manifest_sha256,
                )
            )
        self._raise_if_archived_skills(archived_skills, preset_version_id)
        return resolved

    @staticmethod
    def _raise_if_archived_skills(
        archived_skills: list[str], preset_version_id: uuid.UUID
    ) -> None:
        """Reject resolution when any referenced skill is archived."""
        if archived_skills:
            raise TracecatValidationError(
                "Some skills are archived and cannot be resolved",
                detail={
                    "code": "skill_archived",
                    "skills": sorted(archived_skills),
                    "preset_version_id": str(preset_version_id),
                },
            )

    async def _get_latest_skill_refs_for_preset_version(
        self, preset_version_id: uuid.UUID
    ) -> list[ResolvedSkillRef]:
        """Return current skill versions for a preset version's skill IDs."""

        stmt = (
            select(
                AgentPresetVersionSkill.skill_id,
                Skill.name,
                Skill.current_version_id,
                Skill.deleted_at,
                Skill.archived_at,
                SkillVersion.name,
                SkillVersion.manifest_sha256,
            )
            .join(
                Skill,
                sa.and_(
                    AgentPresetVersionSkill.workspace_id == Skill.workspace_id,
                    AgentPresetVersionSkill.skill_id == Skill.id,
                ),
            )
            .outerjoin(
                SkillVersion,
                sa.and_(
                    SkillVersion.workspace_id == Skill.workspace_id,
                    SkillVersion.skill_id == Skill.id,
                    SkillVersion.id == Skill.current_version_id,
                ),
            )
            .where(
                AgentPresetVersionSkill.workspace_id == self.workspace_id,
                AgentPresetVersionSkill.preset_version_id == preset_version_id,
            )
            .order_by(
                SkillVersion.name.asc().nulls_last(),
                Skill.name.asc(),
                AgentPresetVersionSkill.skill_id.asc(),
            )
        )
        rows = (await self.session.execute(with_deleted(stmt))).tuples().all()
        resolved: list[ResolvedSkillRef] = []
        archived_skills: list[str] = []
        missing_current: list[str] = []
        for (
            skill_id,
            skill_name,
            current_version_id,
            deleted_at,
            archived_at,
            current_version_name,
            manifest_sha256,
        ) in rows:
            if deleted_at is not None or archived_at is not None:
                archived_skills.append(f"{skill_name} ({skill_id})")
                continue
            if current_version_id is None:
                missing_current.append(f"{skill_name} ({skill_id})")
                continue
            if current_version_name is None:
                self._raise_missing_version_name(skill_version_id=current_version_id)
            resolved.append(
                ResolvedSkillRef(
                    skill_id=skill_id,
                    skill_name=current_version_name,
                    skill_version_id=current_version_id,
                    manifest_sha256=manifest_sha256,
                )
            )

        self._raise_if_archived_skills(archived_skills, preset_version_id)
        if missing_current:
            raise TracecatValidationError(
                "Some skills have no current published version",
                detail={
                    "code": "skill_not_published",
                    "skills": sorted(missing_current),
                    "preset_version_id": str(preset_version_id),
                },
            )
        return resolved

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_resolved_skill_ref(
        self, *, skill_id: uuid.UUID, skill_version_id: uuid.UUID
    ) -> ResolvedSkillRef:
        """Return one exact skill ref for a published skill version."""

        stmt = select(
            Skill.id,
            SkillVersion.name,
            SkillVersion.id,
            SkillVersion.manifest_sha256,
        ).where(
            Skill.workspace_id == self.workspace_id,
            Skill.id == skill_id,
            SkillVersion.workspace_id == self.workspace_id,
            SkillVersion.skill_id == Skill.id,
            SkillVersion.id == skill_version_id,
        )
        row = (await self.session.execute(with_deleted(stmt))).tuples().first()
        if row is None:
            raise TracecatNotFoundError(
                f"Skill version '{skill_version_id}' not found for skill '{skill_id}'"
            )
        resolved_skill_id, skill_name, resolved_version_id, manifest_sha256 = row
        if skill_name is None:
            self._raise_missing_version_name(skill_version_id=resolved_version_id)
        return ResolvedSkillRef(
            skill_id=resolved_skill_id,
            skill_name=skill_name,
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
