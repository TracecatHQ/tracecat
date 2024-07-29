from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlmodel import Session, select

from tracecat.auth.credentials import authenticate_user
from tracecat.db.engine import get_session
from tracecat.db.schemas import CaseAction
from tracecat.types.api import CaseActionParams
from tracecat.types.auth import Role

router = APIRouter(prefix="/case-actions")


@router.get("", tags=["cases"])
def list_case_actions(
    role: Annotated[Role, Depends(authenticate_user)],
    session: Session = Depends(get_session),
) -> list[CaseAction]:
    """List all case actions."""
    statement = select(CaseAction).where(
        or_(
            CaseAction.owner_id == "tracecat",
            CaseAction.owner_id == role.user_id,
        )
    )
    actions = session.exec(statement).all()
    return actions


@router.post("", tags=["cases"])
def create_case_action(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CaseActionParams,
    session: Session = Depends(get_session),
) -> CaseAction:
    """Create a new case action."""
    case_action = CaseAction(owner_id=role.user_id, **params.model_dump())
    session.add(case_action)
    session.commit()
    session.refresh(case_action)
    return case_action


@router.delete("/{case_action_id}", tags=["cases"])
def delete_case_action(
    role: Annotated[Role, Depends(authenticate_user)],
    case_action_id: str,
    session: Session = Depends(get_session),
):
    """Delete a case action."""
    statement = select(CaseAction).where(
        CaseAction.owner_id == role.user_id,
        CaseAction.id == case_action_id,
    )
    result = session.exec(statement)
    try:
        action = result.one()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    session.delete(action)
    session.commit()
