import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.cases.dependencies import ExecutorCaseIDPath
from tracecat.cases.rows.exceptions import raise_case_row_link_integrity_error
from tracecat.cases.rows.schemas import (
    CaseTableRowInsertCreate,
    CaseTableRowLinkCreate,
    CaseTableRowRead,
)
from tracecat.cases.rows.service import CaseTableRowsService
from tracecat.cases.schemas import CaseReadMinimal
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(
    prefix="/internal/cases", tags=["internal-cases"], include_in_schema=False
)
linked_cases_router = APIRouter(
    prefix="/internal/tables", tags=["internal-cases"], include_in_schema=False
)


@router.get("/{case_id}/rows")
@require_scope("case:read")
async def list_case_rows(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[CaseTableRowRead]:
    service = CaseTableRowsService(session, role)
    try:
        await service.get_case_or_raise(case_id)
        return await service.list_rows(
            case_id=case_id,
            limit=limit,
            cursor=cursor,
            reverse=reverse,
            include_row_data=True,
        )
    except TracecatNotFoundError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{case_id}/rows", status_code=HTTP_201_CREATED)
@require_scope("case:update")
async def link_case_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
    params: CaseTableRowLinkCreate,
) -> CaseTableRowRead:
    service = CaseTableRowsService(session, role)
    try:
        case = await service.get_case_or_raise(case_id)
        link = await service.link_row(case=case, params=params)
        hydrated = await service._hydrate_links([link], include_row_data=True)
        return hydrated[0]
    except TracecatNotFoundError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError as exc:
        await raise_case_row_link_integrity_error(session, exc)


@router.post("/{case_id}/rows/insert", status_code=HTTP_201_CREATED)
@require_scope("case:update")
async def insert_case_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
    params: CaseTableRowInsertCreate,
) -> CaseTableRowRead:
    service = CaseTableRowsService(session, role)
    try:
        case = await service.get_case_or_raise(case_id)
        link = await service.insert_row_to_case(case=case, params=params)
        hydrated = await service._hydrate_links([link], include_row_data=True)
        return hydrated[0]
    except TracecatNotFoundError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{case_id}/rows/{table_id}/{row_id}", status_code=HTTP_204_NO_CONTENT)
@require_scope("case:update")
async def unlink_case_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: ExecutorCaseIDPath,
    table_id: uuid.UUID,
    row_id: uuid.UUID,
) -> None:
    service = CaseTableRowsService(session, role)
    try:
        case = await service.get_case_or_raise(case_id)
        deleted = await service.unlink_row(case=case, table_id=table_id, row_id=row_id)
        if not deleted:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND, detail="Linked row not found"
            )
    except TracecatNotFoundError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@linked_cases_router.get("/{table_id}/rows/{row_id}/cases")
@require_scope("case:read")
async def list_linked_cases(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    table_id: uuid.UUID,
    row_id: uuid.UUID,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
    order_by: Literal[
        "created_at", "updated_at", "priority", "severity", "status", "tasks"
    ]
    | None = Query(None, description="Case column to order by. Default: created_at"),
    sort: Literal["asc", "desc"] | None = Query(
        None, description="Direction to sort (asc or desc)"
    ),
    exclude_case_id: str | None = Query(
        default=None,
        description="Case ID or short ID (e.g. CASE-0042) to leave out of the results",
    ),
) -> CursorPaginatedResponse[CaseReadMinimal]:
    try:
        excluded_id = (
            await CasesService(session, role).resolve_case_id(exclude_case_id)
            if exclude_case_id is not None
            else None
        )
        return await CaseTableRowsService(session, role).list_linked_cases(
            table_id=table_id,
            row_id=row_id,
            params=CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse),
            exclude_case_id=excluded_id,
            order_by=order_by,
            sort=sort,
        )
    except TracecatValidationError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TracecatNotFoundError as exc:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
