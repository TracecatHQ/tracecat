"""File attachments service for case management."""

import hashlib
import mimetypes
import uuid
from collections.abc import Sequence

import aioboto3
from fastapi import UploadFile
from sqlmodel import select

from tracecat import config
from tracecat.db.schemas import Case, CaseAttachment, File
from tracecat.service import BaseWorkspaceService
from tracecat.types.exceptions import TracecatException

# File type restrictions for security
ALLOWED_MIME_TYPES = {
    # Documents
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "application/rtf",
    # Images
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    # Archives
    "application/zip",
    # Logs and data
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
}

BLOCKED_EXTENSIONS = {
    ".exe",
    ".bat",
    ".sh",
    ".ps1",
    ".cmd",
    ".com",
    ".scr",
    ".pif",
    ".msi",
    ".dll",
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


class FileValidationError(TracecatException):
    """Raised when file validation fails."""


class FileAttachmentsService(BaseWorkspaceService):
    """Service for managing file attachments."""

    service_name = "file_attachments"

    async def _get_s3_client(self):
        """Get configured S3 client (MinIO or AWS S3)."""
        session = aioboto3.Session()

        # Use MinIO configuration if available, otherwise fallback to AWS
        endpoint_url = getattr(config, "MINIO_ENDPOINT_URL", None)
        aws_access_key_id = getattr(config, "MINIO_ACCESS_KEY", None) or getattr(
            config, "AWS_ACCESS_KEY_ID", None
        )
        aws_secret_access_key = getattr(config, "MINIO_SECRET_KEY", None) or getattr(
            config, "AWS_SECRET_ACCESS_KEY", None
        )

        client_kwargs = {
            "service_name": "s3",
            "aws_access_key_id": aws_access_key_id,
            "aws_secret_access_key": aws_secret_access_key,
        }

        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        return session.client(**client_kwargs)

    @property
    def bucket_name(self) -> str:
        """Get the configured bucket name."""
        return getattr(config, "MINIO_BUCKET", "tracecat-files")

    def _validate_file(self, file: UploadFile, content: bytes) -> None:
        """Validate file size, type, and extension."""

        # Check file size
        if len(content) > MAX_FILE_SIZE:
            raise FileValidationError(
                f"File size {len(content)} bytes exceeds maximum {MAX_FILE_SIZE} bytes"
            )

        # Check file extension
        if file.filename:
            file_ext = (
                "." + file.filename.split(".")[-1].lower()
                if "." in file.filename
                else ""
            )
            if file_ext in BLOCKED_EXTENSIONS:
                raise FileValidationError(f"File extension {file_ext} is not allowed")

        # Check MIME type
        if file.content_type and file.content_type not in ALLOWED_MIME_TYPES:
            raise FileValidationError(f"MIME type {file.content_type} is not allowed")

    def _compute_sha256(self, content: bytes) -> str:
        """Compute SHA256 hash of file content."""
        return hashlib.sha256(content).hexdigest()

    def _detect_mime_type(self, filename: str) -> str:
        """Detect MIME type from filename."""
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"

    async def upload_file(self, file: UploadFile) -> File:
        """Upload a file and create a File record.

        Args:
            file: The uploaded file

        Returns:
            Created File record

        Raises:
            FileValidationError: If file validation fails
        """
        # Read file content
        content = await file.read()
        await file.seek(0)  # Reset file pointer

        # Validate file
        self._validate_file(file, content)

        # Compute SHA256 hash
        sha256_hash = self._compute_sha256(content)

        # Check if file already exists (deduplication)
        existing_file = await self._get_file_by_hash(sha256_hash)
        if existing_file and not existing_file.is_deleted:
            return existing_file

        # Detect MIME type
        mime_type = file.content_type or self._detect_mime_type(file.filename or "")

        # Upload to object store
        async with self._get_s3_client() as s3_client:
            blob_path = f"blob/{sha256_hash}"
            await s3_client.put_object(
                Bucket=self.bucket_name,
                Key=blob_path,
                Body=content,
                ContentType=mime_type,
            )

        # Create File record
        file_record = File(
            owner_id=self.workspace_id,
            sha256=sha256_hash,
            original_filename=file.filename or "unknown",
            mime_type=mime_type,
            size_bytes=len(content),
            virus_scan_status="pending",  # TODO: Implement virus scanning
        )

        self.session.add(file_record)
        await self.session.commit()
        await self.session.refresh(file_record)

        return file_record

    async def _get_file_by_hash(self, sha256_hash: str) -> File | None:
        """Get file by SHA256 hash."""
        statement = select(File).where(
            File.owner_id == self.workspace_id,
            File.sha256 == sha256_hash,
        )
        result = await self.session.exec(statement)
        return result.first()

    async def get_file(self, file_id: uuid.UUID) -> File | None:
        """Get file by ID."""
        statement = select(File).where(
            File.owner_id == self.workspace_id,
            File.id == file_id,
        )
        result = await self.session.exec(statement)
        return result.first()

    async def download_file(self, file: File) -> tuple[bytes, str, str]:
        """Download file content from object store.

        Args:
            file: File record to download

        Returns:
            Tuple of (content, filename, mime_type)
        """
        if file.is_deleted:
            raise TracecatException("File has been deleted")

        async with self._get_s3_client() as s3_client:
            response = await s3_client.get_object(
                Bucket=self.bucket_name,
                Key=file.blob_path,
            )
            content = await response["Body"].read()

        return content, file.original_filename, file.mime_type

    async def soft_delete_file(self, file: File) -> None:
        """Mark file for soft deletion."""
        from datetime import datetime

        file.deleted_at = datetime.now()
        self.session.add(file)
        await self.session.commit()

    async def attach_file_to_case(
        self, case: Case, file: File, user_id: uuid.UUID | None = None
    ) -> CaseAttachment:
        """Attach a file to a case.

        Args:
            case: Case to attach file to
            file: File to attach
            user_id: User who is attaching the file

        Returns:
            Created CaseAttachment record
        """
        # Check if attachment already exists
        existing_statement = select(CaseAttachment).where(
            CaseAttachment.case_id == case.id,
            CaseAttachment.file_id == file.id,
        )
        existing = await self.session.exec(existing_statement)
        if existing.first():
            raise TracecatException("File is already attached to this case")

        attachment = CaseAttachment(
            case_id=case.id,
            file_id=file.id,
            attached_by=user_id,
        )

        self.session.add(attachment)
        await self.session.commit()
        await self.session.refresh(attachment)

        return attachment

    async def list_case_attachments(self, case: Case) -> Sequence[CaseAttachment]:
        """List all attachments for a case."""
        statement = (
            select(CaseAttachment)
            .where(CaseAttachment.case_id == case.id)
            .order_by(CaseAttachment.created_at.desc())
        )
        result = await self.session.exec(statement)
        return result.all()

    async def get_case_attachment(
        self, attachment_id: uuid.UUID
    ) -> CaseAttachment | None:
        """Get a specific case attachment."""
        statement = select(CaseAttachment).where(CaseAttachment.id == attachment_id)
        result = await self.session.exec(statement)
        return result.first()

    async def remove_attachment_from_case(self, attachment: CaseAttachment) -> None:
        """Remove file attachment from case (but don't delete the file)."""
        await self.session.delete(attachment)
        await self.session.commit()
