from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.credentials import (
    authenticate_user_for_workspace,
    authenticate_user_or_service_for_workspace,
)
from tracecat.cases.models import (
    CaseCreate,
    CaseEventParams,
    CaseParams,
    CaseRead,
    CaseResponse,
)
from tracecat.cases.service import CaseManagementService
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import Case, CaseEvent
from tracecat.identifiers import CaseID, WorkflowID
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


@router.post("/{case_id}", tags=["cases"])
async def update_case(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    case_id: CaseID,
    params: CaseParams,
) -> CaseResponse:
    """Update a specific case for a workflow."""
    query = select(Case).where(
        Case.owner_id == role.workspace_id,
        Case.workflow_id == workflow_id,
        Case.id == case_id,
    )
    result = await session.exec(query)
    case = result.one_or_none()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    for key, value in params.model_dump(exclude_unset=True).items():
        # Safety: params have been validated
        setattr(case, key, value)

    session.add(case)
    await session.commit()
    await session.refresh(case)
    return CaseResponse(
        id=case.id,
        owner_id=case.owner_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
        workflow_id=case.workflow_id,
        case_title=case.case_title,
        payload=case.payload,
        malice=case.malice,
        status=case.status,
        priority=case.priority,
        action=case.action,
        context=case.context,
        tags=case.tags,
    )


"""Case events"""


@router.post("/{case_id}/events", status_code=status.HTTP_201_CREATED, tags=["cases"])
async def create_case_event(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    case_id: CaseID,
    params: CaseEventParams,
) -> None:
    """Create a new Case Event."""
    case_event = CaseEvent(
        owner_id=role.workspace_id,
        case_id=case_id,
        workflow_id=workflow_id,
        initiator_role=role.type,
        **params.model_dump(),
    )
    session.add(case_event)
    await session.commit()
    await session.refresh(case_event)
    return case_event


@router.get("/{case_id}/events", tags=["cases"])
async def list_case_events(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    case_id: CaseID,
) -> list[CaseEvent]:
    """List all Case Events."""
    query = select(CaseEvent).where(
        CaseEvent.owner_id == role.workspace_id,
        CaseEvent.workflow_id == workflow_id,
        CaseEvent.case_id == case_id,
    )
    result = await session.exec(query)
    case_events = result.all()
    return case_events


@router.get("/{case_id}/events/{event_id}", tags=["cases"])
async def get_case_event(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: WorkflowID,
    case_id: CaseID,
    event_id: str,
):
    """Get a specific case event."""
    query = select(CaseEvent).where(
        CaseEvent.owner_id == role.workspace_id,
        CaseEvent.workflow_id == workflow_id,
        CaseEvent.case_id == case_id,
        CaseEvent.id == event_id,
    )
    result = await session.exec(query)
    case_event = result.one_or_none()
    if case_event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return case_event
