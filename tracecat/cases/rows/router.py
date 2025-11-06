"""Router for case table rows endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.cases.rows.schemas import CaseTableRowLink, CaseTableRowRead
from tracecat.cases.rows.service import CaseTableRowService
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import CaseTableRow
from sqlmodel import select
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.pagination import (
    CursorPaginatedResponse,
    CursorPaginationParams,
)

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
    limit: int = Query(20, ge=1, le=100, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
) -> CursorPaginatedResponse[CaseTableRowRead]:
    """List paginated table rows for a case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseTableRowService(session, role)
    params = CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
    response = await service.list_case_table_rows(case, params)

    # Convert to CaseTableRowRead format
    items = [
        CaseTableRowRead.model_validate(item) for item in response.items
    ]

    return CursorPaginatedResponse(
        items=items,
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
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseTableRowService(session, role)
    row_link = await service.get_case_table_row(case, link_id)
    if row_link is None:
        logger.warning(
            "Case table row link not found",
            case_id=case_id,
            link_id=link_id,
            user_id=role.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case table row link with ID {link_id} not found",
        )

    return CaseTableRowRead.model_validate(row_link)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CaseTableRowRead)
async def link_table_row(
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
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseTableRowService(session, role)
    try:
        link = await service.link_table_row(case, params)

        # Fetch the full row data for response
        row_link_data = await service.get_case_table_row(case, link.id)
        if row_link_data is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch linked row data",
            )

        return CaseTableRowRead.model_validate(row_link_data)
    except TracecatNotFoundError as e:
        logger.warning(
            "Table or row not found",
            case_id=case_id,
            table_id=params.table_id,
            row_id=params.row_id,
            user_id=role.user_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    except TracecatValidationError as e:
        logger.warning(
            "Validation error linking table row",
            case_id=case_id,
            table_id=params.table_id,
            row_id=params.row_id,
            user_id=role.user_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_table_row(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    link_id: uuid.UUID,
) -> None:
    """Unlink a table row from a case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseTableRowService(session, role)

    # Get the link
    stmt = select(CaseTableRow).where(
        CaseTableRow.id == link_id,
        CaseTableRow.case_id == case.id,
    )
    result = await session.exec(stmt)
    case_table_row = result.first()

    if case_table_row is None:
        logger.warning(
            "Case table row link not found",
            case_id=case_id,
            link_id=link_id,
            user_id=role.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case table row link with ID {link_id} not found",
        )

    try:
        await service.unlink_table_row(case_table_row)
    except TracecatNotFoundError as e:
        logger.warning(
            "Case table row not found for unlinking",
            case_id=case_id,
            link_id=link_id,
            user_id=role.user_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e

