import uuid

from fastapi import APIRouter, HTTPException, Query
from starlette.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT, HTTP_404_NOT_FOUND

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.cases.rows.schemas import (
    CaseTableRowInsertCreate,
    CaseTableRowLinkCreate,
    CaseTableRowRead,
)
from tracecat.cases.rows.service import CaseTableRowsService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse

router = APIRouter(
    prefix="/internal/cases", tags=["internal-cases"], include_in_schema=False
)


@router.get("/{case_id}/rows")
@require_scope("case:read")
async def list_case_rows(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
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


@router.post("/{case_id}/rows", status_code=HTTP_201_CREATED)
@require_scope("case:update")
async def link_case_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseTableRowLinkCreate,
) -> CaseTableRowRead:
    service = CaseTableRowsService(session, role)
    case = await service.get_case_or_raise(case_id)
    link = await service.link_row(case=case, params=params)
    hydrated = await service._hydrate_links([link], include_row_data=True)
    return hydrated[0]


@router.post("/{case_id}/rows/insert", status_code=HTTP_201_CREATED)
@require_scope("case:update")
async def insert_case_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseTableRowInsertCreate,
) -> CaseTableRowRead:
    service = CaseTableRowsService(session, role)
    case = await service.get_case_or_raise(case_id)
    link = await service.insert_row_to_case(case=case, params=params)
    hydrated = await service._hydrate_links([link], include_row_data=True)
    return hydrated[0]


@router.delete("/{case_id}/rows/{table_id}/{row_id}", status_code=HTTP_204_NO_CONTENT)
@require_scope("case:update")
async def unlink_case_row(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    table_id: uuid.UUID,
    row_id: uuid.UUID,
) -> None:
    service = CaseTableRowsService(session, role)
    case = await service.get_case_or_raise(case_id)
    deleted = await service.unlink_row(case=case, table_id=table_id, row_id=row_id)
    if not deleted:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail="Linked row not found"
        )
