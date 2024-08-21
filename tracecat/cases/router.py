from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import NoResultFound
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.credentials import (
    authenticate_user_for_workspace,
    authenticate_user_or_service_for_workspace,
)
from tracecat.cases.models import (
    CaseCreate,
    CaseEventCreate,
    CaseRead,
    CaseUpdate,
)
from tracecat.cases.service import CaseEventsService, CaseManagementService
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import CaseEvent
from tracecat.identifiers import CaseEventID, CaseID, WorkflowID
from tracecat.types.auth import Role

router = APIRouter(prefix="/cases")

WorkspaceUserRole = Annotated[Role, Depends(authenticate_user_for_workspace)]
WorkspaceUserOrServiceRole = Annotated[
    Role, Depends(authenticate_user_or_service_for_workspace)
]
AsyncDBSession = Annotated[AsyncSession, Depends(get_async_session)]


@router.post(
    "",
    response_model=CaseRead,
    status_code=status.HTTP_201_CREATED,
    tags=["cases"],
)
async def create_case(
    role: WorkspaceUserOrServiceRole,
    session: AsyncDBSession,
    params: CaseCreate,
) -> CaseRead:
    """Create a new case for a workflow."""
    service = CaseManagementService(session, role=role)
    case = await service.create_case(params)
    return case


@router.get("", response_model=list[CaseRead], tags=["cases"])
async def list_cases(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    # Query params
    workflow_id: WorkflowID | None = Query(None),
    limit: int | None = Query(None),
) -> list[CaseRead]:
    """List all cases for a workflow."""
    service = CaseManagementService(session, role=role)
    return await service.list_cases(workflow_id=workflow_id, limit=limit)


@router.get("/{case_id}", tags=["cases"], response_model=CaseRead)
async def get_case(
    role: WorkspaceUserRole, session: AsyncDBSession, case_id: CaseID
) -> CaseRead:
    """Get a specific case for a workflow."""
    service = CaseManagementService(session, role=role)
    try:
        return await service.get_case(case_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from e


@router.post("/{case_id}", tags=["cases"], response_model=CaseRead)
async def update_case(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: CaseID,
    params: CaseUpdate,
) -> CaseRead:
    """Update a specific case for a workflow."""
    service = CaseManagementService(session, role=role)
    try:
        return await service.update_case(case_id, params)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        ) from e


"""Case events"""


@router.post("/{case_id}/events", status_code=status.HTTP_201_CREATED, tags=["cases"])
async def create_case_event(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: CaseID,
    params: CaseEventCreate,
) -> None:
    """Create a new Case Event."""
    service = CaseEventsService(session, role=role)
    case_event = await service.create_case_event(case_id, params)
    return case_event


@router.get("/{case_id}/events", tags=["cases"])
async def list_case_events(
    role: WorkspaceUserRole, session: AsyncDBSession, case_id: CaseID
) -> list[CaseEvent]:
    """List all Case Events."""
    service = CaseEventsService(session, role=role)
    return await service.list_case_events(case_id)


@router.get("/{case_id}/events/{event_id}", tags=["cases"])
async def get_case_event(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: CaseID,
    event_id: CaseEventID,
):
    """Get a specific case event."""
    service = CaseEventsService(session, role=role)
    try:
        return await service.get_case_event(case_id, event_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case Event not found"
        ) from e
