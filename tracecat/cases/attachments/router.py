"""Router for case attachments endpoints."""

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, status

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.cases.attachments.schemas import (
    CaseAttachmentCreate,
    CaseAttachmentDownloadResponse,
    CaseAttachmentRead,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.storage.exceptions import (
    FileContentMismatchError,
    FileExtensionError,
    FileMimeTypeError,
    FileNameError,
    FileSizeError,
    MaxAttachmentsExceededError,
    StorageLimitExceededError,
)

router = APIRouter(tags=["case-attachments"], prefix="/cases/{case_id}/attachments")

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("")
@require_scope("case:read")
async def list_attachments(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseAttachmentRead]:
    """List all attachments for a case."""
    from tracecat.cases.service import CasesService

    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    attachments = await service.attachments.list_attachments(case)
    return [
        CaseAttachmentRead(
            id=attachment.id,
            case_id=attachment.case_id,
            file_id=attachment.file_id,
            file_name=attachment.file.name,
            content_type=attachment.file.content_type,
            size=attachment.file.size,
            sha256=attachment.file.sha256,
            created_at=attachment.created_at,
            updated_at=attachment.updated_at,
            creator_id=attachment.file.creator_id,
            is_deleted=attachment.file.is_deleted,
        )
        for attachment in attachments
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("case:update")
async def create_attachment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    file: UploadFile,
) -> CaseAttachmentRead:
    """Upload a new attachment to a case."""
    from tracecat.cases.service import CasesService

    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    # Read file content with bounded memory to avoid DoS via large uploads
    try:
        max_size = config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
        chunk_size = 1024 * 1024  # 1 MB chunks

        # Reset file pointer to beginning to ensure we read the full content
        await file.seek(0)

        content_buf = bytearray()
        hasher = hashlib.sha256()
        total_read = 0

        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > max_size:
                logger.warning(
                    "Upload exceeds maximum attachment size",
                    case_id=case_id,
                    filename=file.filename,
                    max_size=max_size,
                )
                # Raise domain error to preserve existing error mapping/structure
                raise FileSizeError("File exceeds maximum allowed size")
            hasher.update(chunk)
            content_buf.extend(chunk)

        # Validate that we actually read content
        if total_read == 0:
            raise ValueError("File appears to be empty or unreadable")

        # Validate size consistency if available
        if hasattr(file, "size") and file.size and total_read != file.size:
            logger.warning(
                "File size mismatch during upload",
                case_id=case_id,
                filename=file.filename,
                declared_size=file.size,
                actual_size=total_read,
            )

        # Comprehensive debugging for upload
        logger.info(
            "File upload - content read",
            case_id=case_id,
            filename=file.filename,
            declared_content_type=file.content_type,
            declared_size=getattr(file, "size", "unknown"),
            actual_size=total_read,
            content_hash=hasher.hexdigest()[:16] if total_read else "empty",
        )
        content = bytes(content_buf)

    except HTTPException:
        # Re-raise HTTPExceptions without wrapping
        raise
    except Exception as e:
        logger.error(
            "Failed to read file content",
            case_id=case_id,
            filename=file.filename,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read file content: {str(e)}",
        ) from e

    # Create attachment
    try:
        params = CaseAttachmentCreate(
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            size=len(content),  # Use actual read size
            content=content,
        )

        logger.info(
            "Creating attachment",
            case_id=case_id,
            file_name=params.file_name,
            content_type=params.content_type,
            size=params.size,
            content_hash=hashlib.sha256(params.content).hexdigest()[:16],
        )

        attachment = await service.attachments.create_attachment(case, params)

        logger.info(
            "Attachment created successfully",
            case_id=case_id,
            attachment_id=attachment.id,
            file_id=attachment.file_id,
            stored_size=attachment.file.size,
            stored_sha256=attachment.file.sha256[:16],
            size_mb=round(attachment.file.size / (1024 * 1024), 2),
        )

        return CaseAttachmentRead(
            id=attachment.id,
            case_id=attachment.case_id,
            file_id=attachment.file_id,
            file_name=attachment.file.name,
            content_type=attachment.file.content_type,
            size=attachment.file.size,
            sha256=attachment.file.sha256,
            created_at=attachment.created_at,
            updated_at=attachment.updated_at,
            creator_id=attachment.file.creator_id,
            is_deleted=attachment.file.is_deleted,
        )
    except FileExtensionError as e:
        logger.error(
            "File extension validation error",
            case_id=case_id,
            filename=file.filename,
            extension=e.extension,
            allowed_extensions=e.allowed_extensions,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error": "unsupported_file_extension",
                "message": str(e),
                "extension": e.extension,
                "allowed_extensions": e.allowed_extensions,
            },
        ) from e
    except FileMimeTypeError as e:
        logger.error(
            "Content type validation error",
            case_id=case_id,
            filename=file.filename,
            content_type=e.mime_type,
            allowed_types=e.allowed_types,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error": "unsupported_content_type",
                "message": str(e),
                "content_type": e.mime_type,
                "allowed_types": e.allowed_types,
            },
        ) from e
    except FileSizeError as e:
        logger.error(
            "File size validation error",
            case_id=case_id,
            filename=file.filename,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "file_too_large",
                "message": str(e),
            },
        ) from e
    except (FileContentMismatchError, FileNameError) as e:
        logger.error(
            "File validation error",
            case_id=case_id,
            filename=file.filename,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "file_validation_failed",
                "message": str(e),
            },
        ) from e
    except MaxAttachmentsExceededError as e:
        logger.error(
            "Maximum attachments per case exceeded",
            case_id=case_id,
            filename=file.filename,
            current_count=e.current_count,
            max_count=e.max_count,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "max_attachments_exceeded",
                "message": str(e),
                "current_count": e.current_count,
                "max_count": e.max_count,
            },
        ) from e
    except StorageLimitExceededError as e:
        current_mb = e.current_size / 1024 / 1024
        new_mb = e.new_file_size / 1024 / 1024
        max_mb = e.max_size / 1024 / 1024
        logger.error(
            "Case storage limit exceeded",
            case_id=case_id,
            filename=file.filename,
            current_size_mb=round(current_mb, 2),
            new_file_size_mb=round(new_mb, 2),
            max_size_mb=round(max_mb, 2),
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "storage_limit_exceeded",
                "message": str(e),
                "current_size_mb": round(current_mb, 2),
                "new_file_size_mb": round(new_mb, 2),
                "max_size_mb": round(max_mb, 2),
            },
        ) from e
    except Exception as e:
        logger.error(
            "Failed to create attachment",
            case_id=case_id,
            filename=file.filename,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload attachment.",
        ) from e


@router.get("/{attachment_id}")
@require_scope("case:read")
async def download_attachment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    attachment_id: uuid.UUID,
    request: Request,
    preview: bool = Query(
        False, description="If true, allows inline preview for safe image types"
    ),
) -> CaseAttachmentDownloadResponse:
    """Download an attachment."""
    from tracecat.cases.service import CasesService

    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, attachment_id=attachment_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    try:
        (
            presigned_url,
            filename,
            content_type,
        ) = await service.attachments.get_attachment_download_url(
            case,
            attachment_id,
            preview=preview,
        )
        return CaseAttachmentDownloadResponse(
            download_url=presigned_url,
            file_name=filename,
            content_type=content_type,
        )

    except Exception as e:
        error_type = type(e).__name__
        if "not found" in str(e).lower():
            logger.warning(
                "Attachment not found",
                case_id=case_id,
                attachment_id=attachment_id,
                error=str(e),
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e

        logger.error(
            "Failed to generate download URL",
            case_id=case_id,
            attachment_id=attachment_id,
            error=str(e),
            error_type=error_type,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate download URL: {str(e)}",
        ) from e


@router.delete(
    "/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
@require_scope("case:update")
async def delete_attachment(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> None:
    """Delete an attachment (soft delete)."""
    from tracecat.cases.service import CasesService

    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, attachment_id=attachment_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    try:
        await service.attachments.delete_attachment(case, attachment_id)
    except Exception as e:
        error_type = type(e).__name__

        if "not found" in str(e).lower():
            logger.warning(
                "Attachment not found",
                case_id=case_id,
                attachment_id=attachment_id,
                error=str(e),
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        if "permission" in str(e).lower():
            logger.warning(
                "Permission denied",
                case_id=case_id,
                attachment_id=attachment_id,
                user_id=role.user_id,
                error=str(e),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(e),
            ) from e

        logger.error(
            "Failed to delete attachment",
            case_id=case_id,
            attachment_id=attachment_id,
            error=str(e),
            error_type=error_type,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete attachment: {str(e)}",
        ) from e
