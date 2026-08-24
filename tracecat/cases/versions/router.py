"""Router for case version history endpoints."""

import uuid

from fastapi import APIRouter, HTTPException, Query
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.cases.enums import CaseVersionField
from tracecat.cases.service import CasesService
from tracecat.cases.versions.schemas import (
    CaseVersionCompareRead,
    CaseVersionReadMinimal,
    CaseVersionRestoreRead,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, PageParams

router = APIRouter(prefix="/{case_id}/versions")


@router.get(
    "",
    response_model=CursorPaginatedResponse[CaseVersionReadMinimal],
)
@require_scope("case:read")
async def list_case_versions(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
        description="Maximum items per page",
    ),
    cursor: str | None = Query(
        None,
        max_length=8192,
        description="Cursor for pagination",
    ),
    field: CaseVersionField | None = Query(
        None,
        description="Optionally include only summary or description versions",
    ),
) -> CursorPaginatedResponse[CaseVersionReadMinimal]:
    """List immutable case field versions newest-first."""
    service = CasesService(session, role)
    if not await service.case_exists(case_id):
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )
    try:
        return await service.versions.list_versions(
            case_id=case_id,
            page=PageParams(limit=limit, cursor=cursor),
            field=field,
        )
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=exc.detail or str(exc),
        ) from exc


@router.get(
    "/{version_id}/compare",
    response_model=CaseVersionCompareRead,
)
@require_scope("case:read")
async def compare_case_version(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    version_id: uuid.UUID,
) -> CaseVersionCompareRead:
    """Compare a case field version with its immediate predecessor."""
    service = CasesService(session, role)
    comparison = await service.versions.compare_with_predecessor(
        case_id=case_id,
        version_id=version_id,
    )
    if comparison is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Case version '{version_id}' not found",
        )
    return comparison


@router.post(
    "/{version_id}/restore",
    response_model=CaseVersionRestoreRead,
)
@require_scope("case:update")
async def restore_case_version(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    version_id: uuid.UUID,
) -> CaseVersionRestoreRead:
    """Restore one historical case field version atomically."""
    try:
        return await CasesService(session, role).restore_version(
            case_id=case_id,
            version_id=version_id,
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
