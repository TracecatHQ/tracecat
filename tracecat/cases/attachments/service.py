"""Service for managing case attachments."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.attachments.schemas import CaseAttachmentCreate
from tracecat.cases.schemas import AttachmentCreatedEvent, AttachmentDeletedEvent
from tracecat.contexts import ctx_run
from tracecat.db.models import Case, CaseAttachment, File, Workspace
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatException,
    TracecatNotFoundError,
)
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.storage import blob
from tracecat.storage.exceptions import (
    FileSizeError,
    MaxAttachmentsExceededError,
    StorageLimitExceededError,
)
from tracecat.storage.validation import FileSecurityValidator


class CaseAttachmentService(BaseWorkspaceService):
    """Service for managing case attachments."""

    service_name = "case_attachments"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self._workspace_cache: Workspace | None = None

    @staticmethod
    def _compute_sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @property
    def attachments_bucket(self) -> str:
        """Centralized accessor for the attachments blob bucket."""
        return config.TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS

    @staticmethod
    def _storage_key_for(sha256: str) -> str:
        """Build the canonical storage key for an attachment content hash."""
        return f"attachments/{sha256}"

    async def _upload_to_attachments_bucket(
        self, *, content: bytes, sha256: str, content_type: str
    ) -> None:
        """Upload file bytes to the attachments bucket with standard error handling."""
        try:
            await blob.upload_file(
                content=content,
                key=self._storage_key_for(sha256),
                bucket=self.attachments_bucket,
                content_type=content_type,
            )
        except Exception as e:
            # Rollback the database transaction if storage fails to keep DB/storage in sync
            await self.session.rollback()
            raise TracecatException(f"Failed to upload file: {str(e)}") from e

    async def _emit_case_event(
        self, case: Case, event: AttachmentCreatedEvent | AttachmentDeletedEvent
    ) -> None:
        """Emit a case event with run context, avoiding circular imports at module import time."""
        run_ctx = ctx_run.get()
        # Import here to avoid circular dependency
        from tracecat.cases.service import CaseEventsService

        # Attach workflow execution id if available
        if hasattr(event, "wf_exec_id"):
            event.wf_exec_id = run_ctx.wf_exec_id if run_ctx else None

        await CaseEventsService(self.session, self.role).create_event(
            case=case,
            event=event,
        )

    async def _get_workspace(self) -> Workspace:
        """Get the workspace for the current context, with caching."""
        if self._workspace_cache is None:
            result = await self.session.execute(
                select(Workspace).where(Workspace.id == self.workspace_id)
            )
            workspace = result.scalar_one_or_none()
            if not workspace:
                raise TracecatException(f"Workspace {self.workspace_id} not found")
            self._workspace_cache = workspace
        return self._workspace_cache

    async def _assert_case_limits(self, case: Case, new_size: int) -> None:
        """Validate case-level constraints for attachments (count and total storage)."""
        # Use COUNT(*) with join on non-deleted files (matches list_attachments semantics)
        count_stmt = (
            select(func.count())
            .select_from(CaseAttachment)
            .join(File, cast(CaseAttachment.file_id, sa.UUID) == cast(File.id, sa.UUID))
            .where(CaseAttachment.case_id == case.id, File.deleted_at.is_(None))
        )
        count_result = await self.session.execute(count_stmt)
        current_attachment_count = int(count_result.scalar_one() or 0)
        if current_attachment_count >= config.TRACECAT__MAX_ATTACHMENTS_PER_CASE:
            raise MaxAttachmentsExceededError(
                f"Case already has {current_attachment_count} attachments. "
                f"Maximum allowed is {config.TRACECAT__MAX_ATTACHMENTS_PER_CASE}",
                current_count=current_attachment_count,
                max_count=config.TRACECAT__MAX_ATTACHMENTS_PER_CASE,
            )

        current_storage = await self.get_total_storage_used(case)
        if current_storage + new_size > config.TRACECAT__MAX_CASE_STORAGE_BYTES:
            current_mb = current_storage / 1024 / 1024
            new_mb = new_size / 1024 / 1024
            max_mb = config.TRACECAT__MAX_CASE_STORAGE_BYTES / 1024 / 1024
            raise StorageLimitExceededError(
                f"Adding this file ({new_mb:.1f}MB) would exceed the case storage limit. "
                f"Current usage: {current_mb:.1f}MB, Maximum allowed: {max_mb:.1f}MB",
                current_size=current_storage,
                new_file_size=new_size,
                max_size=config.TRACECAT__MAX_CASE_STORAGE_BYTES,
            )

    @staticmethod
    def _verify_integrity(content: bytes, expected_sha256: str) -> None:
        computed_hash = hashlib.sha256(content).hexdigest()
        if computed_hash != expected_sha256:
            raise TracecatException("File integrity check failed")

    async def list_attachments(self, case: Case) -> Sequence[CaseAttachment]:
        """List all attachments for a case.

        Args:
            case: The case to list attachments for

        Returns:
            List of case attachments
        """
        statement = (
            select(CaseAttachment)
            .join(File, cast(CaseAttachment.file_id, sa.UUID) == cast(File.id, sa.UUID))
            .where(CaseAttachment.case_id == case.id, File.deleted_at.is_(None))
            .options(selectinload(CaseAttachment.file))
            .order_by(CaseAttachment.created_at.desc())
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_attachment(
        self, case: Case, attachment_id: uuid.UUID
    ) -> CaseAttachment | None:
        """Get a specific attachment for a case.

        Args:
            case: The case the attachment belongs to
            attachment_id: The attachment ID

        Returns:
            The attachment if found, None otherwise
        """
        # Single query join to ensure file not soft-deleted; eager-load relationship
        statement = (
            select(CaseAttachment)
            .join(File, cast(CaseAttachment.file_id, sa.UUID) == cast(File.id, sa.UUID))
            .where(
                CaseAttachment.case_id == case.id,
                CaseAttachment.id == attachment_id,
                File.deleted_at.is_(None),
            )
            .options(selectinload(CaseAttachment.file))
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def _require_attachment(
        self, case: Case, attachment_id: uuid.UUID
    ) -> CaseAttachment:
        attachment = await self.get_attachment(case, attachment_id)
        if not attachment:
            raise TracecatNotFoundError(f"Attachment {attachment_id} not found")
        return attachment

    async def create_attachment(
        self, case: Case, params: CaseAttachmentCreate
    ) -> CaseAttachment:
        """Create a new attachment for a case with security validations.

        Args:
            case: The case to attach the file to
            params: The attachment parameters

        Returns:
            The created attachment

        Raises:
            ValueError: If validation fails
            TracecatException: If storage operation fails
        """

        # Calculate actual size from content to prevent client-controlled size bypass
        actual_size = len(params.content)

        # Security check: Verify declared size matches actual content size
        if params.size != actual_size:
            logger.warning(
                "Size mismatch detected in attachment upload",
                case_id=case.id,
                declared_size=params.size,
                actual_size=actual_size,
                filename=params.file_name,
                user_id=self.role.user_id
                if self.role and self.role.type == "user"
                else None,
            )
            # Use actual size for all validations to prevent bypass

        # Validate file size limits using actual content size
        if actual_size > config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES:
            raise FileSizeError(
                f"File size ({actual_size / 1024 / 1024:.1f}MB) exceeds maximum allowed size "
                f"({config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES / 1024 / 1024}MB)"
            )

        # Validate case-level limits (count + storage) efficiently using actual size
        await self._assert_case_limits(case, actual_size)

        # Get workspace settings for attachment validation
        workspace = await self._get_workspace()
        workspace_settings = workspace.settings or {}

        # Use workspace-specific allowed extensions/MIME types if configured, otherwise use defaults
        allowed_extensions = workspace_settings.get("allowed_attachment_extensions")
        if allowed_extensions:
            allowed_extensions = set(allowed_extensions)

        allowed_mime_types = workspace_settings.get("allowed_attachment_mime_types")
        if allowed_mime_types:
            allowed_mime_types = set(allowed_mime_types)

        # Get magic number validation setting (default to True for security)
        validate_magic_number = workspace_settings.get(
            "validate_attachment_magic_number"
        )
        if validate_magic_number is None:
            validate_magic_number = True

        # Comprehensive security validation using the new validator
        # Ensure allowed lists are Sequences for type checking
        validator = FileSecurityValidator(
            allowed_extensions=(
                list(allowed_extensions) if allowed_extensions else None
            ),
            allowed_mime_types=(
                list(allowed_mime_types) if allowed_mime_types else None
            ),
            validate_magic_number=validate_magic_number,
        )
        # Strip "Content-Type" from the declared MIME type
        declared_mime_type = params.content_type.split(";")[0].strip()
        validation_result = validator.validate_file(
            content=params.content,
            filename=params.file_name,
            declared_mime_type=declared_mime_type,
        )

        # Compute content hash for deduplication and integrity
        sha256 = self._compute_sha256(params.content)

        # Determine uploader ID (may be None for workflow/service uploads)
        creator_id: uuid.UUID | None = (
            self.role.user_id if self.role.type == "user" else None
        )

        # Check if file already exists (deduplication)
        existing_file = await self.session.execute(
            select(File).where(
                File.sha256 == sha256,
                File.workspace_id == self.workspace_id,
            )
        )
        file = existing_file.scalars().first()

        restored = False
        if not file:
            # Create new file record
            file = File(
                workspace_id=self.workspace_id,
                sha256=sha256,
                name=validation_result.filename,
                content_type=validation_result.content_type,
                size=actual_size,
                creator_id=creator_id,
            )
            self.session.add(file)
            await self.session.flush()
            # Upload to blob storage
            await self._upload_to_attachments_bucket(
                content=params.content,
                sha256=sha256,
                content_type=validation_result.content_type,
            )
        else:
            # Verify existing file's recorded size matches actual content
            if file.size != actual_size:
                logger.error(
                    "Security: Existing file size mismatch detected",
                    case_id=case.id,
                    file_id=file.id,
                    sha256=sha256,
                    recorded_size=file.size,
                    actual_size=actual_size,
                    filename=params.file_name,
                )
                # Update the file record with correct size
                file.size = actual_size

            # If the file entity exists but has been soft deleted, restore and re-upload
            if file.deleted_at is not None:
                file.deleted_at = None
                restored = True
                await self._upload_to_attachments_bucket(
                    content=params.content,
                    sha256=sha256,
                    content_type=validation_result.content_type,
                )

        # Check if attachment already exists for this case and file
        existing_attachment = await self.session.execute(
            select(CaseAttachment)
            .where(
                CaseAttachment.case_id == case.id,
                CaseAttachment.file_id == file.id,
            )
            .options(selectinload(CaseAttachment.file))
        )
        attachment = existing_attachment.scalars().first()

        should_create_event = False
        if attachment:
            # Attachment already exists and is active - return it
            # (If the underlying file was restored above, emit restoration event)
            if (
                not restored
                and file.deleted_at is None
                and getattr(attachment.file, "deleted_at", None) is None
            ):
                return attachment
            # Ensure relationship is consistent
            attachment.file = file
            should_create_event = True
        else:
            # Create new attachment link
            attachment = CaseAttachment(
                case_id=case.id,
                file_id=file.id,
            )
            # Eagerly link the file relationship to avoid lazy loading in async contexts
            attachment.file = file
            self.session.add(attachment)
            should_create_event = True  # New attachment event

        # Flush to ensure the attachment gets an ID
        await self.session.flush()

        # Record attachment event (for new attachments or restorations)
        if should_create_event:
            await self._emit_case_event(
                case,
                AttachmentCreatedEvent(
                    attachment_id=attachment.id,
                    file_name=file.name,
                    content_type=file.content_type,
                    size=file.size,  # This now uses the actual size from the File record
                    wf_exec_id=None,  # populated by _emit_case_event if run context available
                ),
            )

        await self.session.commit()
        # Reload attachment with the file relationship eagerly loaded
        await self.session.refresh(attachment, attribute_names=["file"])
        return attachment

    async def download_attachment(
        self, case: Case, attachment_id: uuid.UUID
    ) -> tuple[bytes, str, str]:
        """Download an attachment's content.

        Args:
            case: The case the attachment belongs to
            attachment_id: The attachment ID

        Returns:
            Tuple of (content, filename, content_type)

        Raises:
            TracecatNotFoundError: If attachment not found
            TracecatException: If download fails
        """

        attachment = await self._require_attachment(case, attachment_id)

        # Download from blob storage
        storage_key = attachment.storage_path
        try:
            content = await blob.download_file(
                key=storage_key,
                bucket=self.attachments_bucket,
            )

            # Verify integrity
            self._verify_integrity(content, attachment.file.sha256)

            return content, attachment.file.name, attachment.file.content_type
        except FileNotFoundError as e:
            raise TracecatNotFoundError("Attachment file not found in storage") from e
        except Exception as e:
            raise TracecatException(f"Failed to download attachment: {str(e)}") from e

    async def get_attachment_download_url(
        self,
        case: Case,
        attachment_id: uuid.UUID,
        preview: bool = False,
        expiry: int | None = None,
    ) -> tuple[str, str, str]:
        """Generate a presigned URL for downloading an attachment.

        Args:
            case: The case the attachment belongs to
            attachment_id: The attachment ID
            preview: If true, allows inline preview for safe image types (deprecated, kept for compatibility)
            expiry: URL expiry time in seconds (defaults to config value)

        Returns:
            Tuple of (presigned_url, filename, content_type)

        Raises:
            TracecatNotFoundError: If attachment not found
            TracecatException: If URL generation fails
        """

        attachment = await self._require_attachment(case, attachment_id)

        # Generate presigned URL for blob storage
        storage_key = attachment.storage_path

        # Security: Always force download for attachments (no preview; param retained for compatibility)
        force_download = True
        override_content_type = "application/octet-stream"

        try:
            presigned_url = await blob.generate_presigned_download_url(
                key=storage_key,
                bucket=self.attachments_bucket,
                expiry=expiry,
                force_download=force_download,
                override_content_type=override_content_type,
            )
            return presigned_url, attachment.file.name, attachment.file.content_type
        except Exception as e:
            raise TracecatException(f"Failed to generate download URL: {str(e)}") from e

    async def delete_attachment(self, case: Case, attachment_id: uuid.UUID) -> None:
        """Soft delete an attachment.

        Implements soft deletion where the file is removed from blob storage
        but the database record is preserved with a deletion timestamp.

        Args:
            case: The case the attachment belongs to
            attachment_id: The attachment ID

        Raises:
            TracecatNotFoundError: If attachment not found
            TracecatAuthorizationError: If user lacks permission
        """

        attachment = await self._require_attachment(case, attachment_id)

        # Check if user has permission (must be creator or admin)
        # Service roles with admin access can delete any attachment
        # TODO: This is a hack to allow service roles to delete attachments
        # We should use API endpoint level permissions instead
        if (
            self.role.type == "user"
            and attachment.file.creator_id != self.role.user_id
            and not self.role.is_privileged
        ):
            raise TracecatAuthorizationError(
                "You don't have permission to delete this attachment"
            )

        # Soft delete the file
        attachment.file.deleted_at = datetime.now(UTC)

        # Delete from blob storage
        storage_key = attachment.storage_path
        try:
            await blob.delete_file(
                key=storage_key,
                bucket=self.attachments_bucket,
            )
        except Exception as e:
            # Log but don't fail - we've already marked as deleted
            logger.error(
                "Failed to delete file from blob storage",
                attachment_id=attachment_id,
                storage_key=storage_key,
                error=str(e),
            )

        # Record deletion event
        await self._emit_case_event(
            case,
            AttachmentDeletedEvent(
                attachment_id=attachment_id,
                file_name=attachment.file.name,
                wf_exec_id=None,  # populated by _emit_case_event if run context available
            ),
        )

        await self.session.commit()

    async def get_total_storage_used(self, case: Case) -> int:
        """Get total storage used by a case's attachments.

        Args:
            case: The case to check

        Returns:
            Total bytes used
        """
        statement = (
            select(func.sum(File.size))
            .select_from(File)
            .join(
                CaseAttachment,
                cast(File.id, sa.UUID) == cast(CaseAttachment.file_id, sa.UUID),
            )
            .where(
                CaseAttachment.case_id == case.id,
                File.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one() or 0
