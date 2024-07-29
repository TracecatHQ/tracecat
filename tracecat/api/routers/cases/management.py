from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.auth.credentials import authenticate_service, authenticate_user
from tracecat.db.engine import get_session
from tracecat.db.schemas import Case, CaseEvent
from tracecat.types.api import CaseEventParams, CaseParams, CaseResponse
from tracecat.types.auth import Role

router = APIRouter()


@router.post(
    "/workflows/{workflow_id}/cases",
    status_code=status.HTTP_201_CREATED,
    tags=["cases"],
)
def create_case(
    role: Annotated[Role, Depends(authenticate_service)],  # M2M
    workflow_id: str,
    cases: list[CaseParams],
    session: Session = Depends(get_session),
) -> CaseResponse:
    """Create a new case for a workflow."""
    case = Case(
        owner_id=role.user_id,
        workflow_id=workflow_id,
        **cases.model_dump(),
    )
    session.add(case)
    session.commit()
    session.refresh(case)

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


@router.get("/workflows/{workflow_id}/cases", tags=["cases"])
def list_cases(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> list[CaseResponse]:
    """List all cases for a workflow."""
    query = select(Case).where(
        Case.owner_id == role.user_id, Case.workflow_id == workflow_id
    )
    result = session.exec(query)
    try:
        cases = result.fetchmany(size=limit)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    return [
        CaseResponse(
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
        for case in cases
    ]


@router.get("/workflows/{workflow_id}/cases/{case_id}", tags=["cases"])
def get_case(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    session: Session = Depends(get_session),
) -> CaseResponse:
    """Get a specific case for a workflow."""
    query = select(Case).where(
        Case.owner_id == role.user_id,
        Case.workflow_id == workflow_id,
        Case.id == case_id,
    )
    case = session.exec(query).one_or_none()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
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


@router.post("/workflows/{workflow_id}/cases/{case_id}", tags=["cases"])
def update_case(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    params: CaseParams,
    session: Session = Depends(get_session),
) -> CaseResponse:
    """Update a specific case for a workflow."""
    query = select(Case).where(
        Case.owner_id == role.user_id,
        Case.workflow_id == workflow_id,
        Case.id == case_id,
    )
    case = session.exec(query).one_or_none()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    for key, value in params.model_dump(exclude_unset=True).items():
        # Safety: params have been validated
        setattr(case, key, value)

    session.add(case)
    session.commit()
    session.refresh(case)
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


@router.post(
    "/workflows/{workflow_id}/cases/{case_id}/events",
    status_code=status.HTTP_201_CREATED,
    tags=["cases"],
)
def create_case_event(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    params: CaseEventParams,
    session: Session = Depends(get_session),
) -> None:
    """Create a new Case Event."""
    case_event = CaseEvent(
        owner_id=role.user_id,
        case_id=case_id,
        workflow_id=workflow_id,
        initiator_role=role.type,
        **params.model_dump(),
    )
    session.add(case_event)
    session.commit()
    session.refresh(case_event)
    return case_event


@router.get("/workflows/{workflow_id}/cases/{case_id}/events", tags=["cases"])
def list_case_events(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    session: Session = Depends(get_session),
) -> list[CaseEvent]:
    """List all Case Events."""
    query = select(CaseEvent).where(
        CaseEvent.owner_id == role.user_id,
        CaseEvent.workflow_id == workflow_id,
        CaseEvent.case_id == case_id,
    )
    case_events = session.exec(query).all()
    return case_events


@router.get(
    "/workflows/{workflow_id}/cases/{case_id}/events/{event_id}", tags=["cases"]
)
def get_case_event(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    case_id: str,
    event_id: str,
    session: Session = Depends(get_session),
):
    """Get a specific case event."""
    query = select(CaseEvent).where(
        CaseEvent.owner_id == role.user_id,
        CaseEvent.workflow_id == workflow_id,
        CaseEvent.case_id == case_id,
        CaseEvent.id == event_id,
    )
    case_event = session.exec(query).one_or_none()
    if case_event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return case_event
