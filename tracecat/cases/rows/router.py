"""Router for case table rows endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.cases.rows.schemas import CaseTableRowLink, CaseTableRowRead
from tracecat.cases.rows.service import CaseTableRowService
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import CaseTableRow
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(tags=["case-table-rows"], prefix="/cases/{case_id}/rows")

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("", response_model=CursorPaginatedResponse[CaseTableRowRead])
async def list_case_table_rows(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=200, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
) -> CursorPaginatedResponse[CaseTableRowRead]:
    """List paginated table rows for a case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseTableRowService(session, role)
    try:
        response = await service.list_case_table_rows(
            case, CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return CursorPaginatedResponse(
        items=[CaseTableRowRead.model_validate(item) for item in response.items],
        next_cursor=response.next_cursor,
        prev_cursor=response.prev_cursor,
        has_more=response.has_more,
        has_previous=response.has_previous,
        total_estimate=response.total_estimate,
    )


@router.get("/{link_id}", response_model=CaseTableRowRead)
async def get_case_table_row(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    link_id: uuid.UUID,
) -> CaseTableRowRead:
    """Get a specific case table row link."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseTableRowService(session, role)
    row_link = await service.get_case_table_row(case, link_id)
    if row_link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case table row link with ID {link_id} not found",
        )
    return CaseTableRowRead.model_validate(row_link)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CaseTableRowRead)
async def link_case_table_row(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseTableRowLink,
) -> CaseTableRowRead:
    """Link an existing table row to a case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseTableRowService(session, role)
    try:
        link = await service.link_case_row(case, params)
        row_link = await service.get_case_table_row(case, link.id)
        if row_link is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch linked row data",
            )
        return CaseTableRowRead.model_validate(row_link)
    except TracecatNotFoundError as exc:
        logger.warning(
            "Failed to link case table row",
            case_id=case_id,
            table_id=params.table_id,
            row_id=params.row_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_case_table_row(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    link_id: uuid.UUID,
) -> None:
    """Unlink a table row link from a case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    stmt = sa.select(CaseTableRow).where(
        CaseTableRow.workspace_id == role.workspace_id,
        CaseTableRow.case_id == case.id,
        CaseTableRow.id == link_id,
    )
    result = await session.execute(stmt)
    link = result.scalars().first()
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case table row link with ID {link_id} not found",
        )

    service = CaseTableRowService(session, role)
    await service.unlink_case_row(case, link)
