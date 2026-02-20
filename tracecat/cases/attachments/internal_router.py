"""Executor/internal router for case attachments endpoints."""

from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.cases.attachments.schemas import (
    CaseAttachmentCreate,
    CaseAttachmentRead,
    InternalCaseAttachmentDownloadResponse,
)
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(
    tags=["internal-case-attachments"],
    prefix="/internal/cases/{case_id}/attachments",
    include_in_schema=False,
)


class ExecutorAttachmentCreateRequest(BaseModel):
    filename: str
    content_base64: str
    content_type: str = "application/octet-stream"


@router.get("")
@require_scope("case:read")
async def list_attachments(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseAttachmentRead]:
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
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: ExecutorAttachmentCreateRequest,
) -> CaseAttachmentRead:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    try:
        content = base64.b64decode(params.content_base64, validate=True)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base64 content: {str(e)}",
        ) from e

    attachment = await service.attachments.create_attachment(
        case,
        CaseAttachmentCreate(
            file_name=params.filename or "unnamed",
            content_type=params.content_type or "application/octet-stream",
            size=len(content),
            content=content,
        ),
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


@router.get("/{attachment_id}")
@require_scope("case:read")
async def get_attachment_download_info(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    attachment_id: uuid.UUID,
    request: Request,
    preview: bool = Query(
        False, description="If true, allows inline preview for safe image types"
    ),
    expiry: int | None = Query(None, description="Optional URL expiry time in seconds"),
) -> InternalCaseAttachmentDownloadResponse:
    _ = request

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
            expiry=expiry,
        )
        return InternalCaseAttachmentDownloadResponse(
            id=attachment_id,
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


@router.get("/{attachment_id}/download")
@require_scope("case:read")
async def download_attachment_content(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> dict[str, str]:
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    try:
        content, filename, content_type = await service.attachments.download_attachment(
            case, attachment_id
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    return {
        "content_base64": base64.b64encode(content).decode("utf-8"),
        "file_name": filename,
        "content_type": content_type,
    }


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("case:update")
async def delete_attachment(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> None:
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


# --- Simplified routes for SDK/UDF use cases ---


@router.get("/{attachment_id}/metadata")
@require_scope("case:read")
async def get_attachment_metadata(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> CaseAttachmentRead:
    """Get attachment metadata without download URL.

    Returns attachment metadata in a simple format suitable for SDK/UDF use.
    """
    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    attachment = await service.attachments.get_attachment(case, attachment_id)
    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment {attachment_id} not found",
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
    )


@router.get("/{attachment_id}/url")
@require_scope("case:read")
async def get_attachment_url(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    attachment_id: uuid.UUID,
    expiry: int | None = Query(None, description="Optional URL expiry time in seconds"),
) -> str:
    """Get a presigned download URL for an attachment.

    Returns just the URL string.
    """
    # Validate expiry if provided
    if expiry is not None:
        if expiry <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expiry must be a positive number of seconds",
            )
        if expiry > 86400:  # 24 hours
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expiry cannot exceed 24 hours (86400 seconds)",
            )

    service = CasesService(session, role)
    case = await service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    try:
        download_url, _, _ = await service.attachments.get_attachment_download_url(
            case=case,
            attachment_id=attachment_id,
            expiry=expiry,
        )
        return download_url
    except Exception as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate download URL: {str(e)}",
        ) from e
