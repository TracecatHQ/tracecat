from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlmodel import Session, select

from tracecat.auth.credentials import authenticate_user
from tracecat.db.engine import get_session
from tracecat.db.schemas import CaseContext
from tracecat.types.api import CaseContextParams
from tracecat.types.auth import Role

router = APIRouter(prefix="/case-contexts")


@router.get("", tags=["cases"])
def list_case_contexts(
    role: Annotated[Role, Depends(authenticate_user)],
    session: Session = Depends(get_session),
) -> list[CaseContext]:
    """List all case contexts."""
    statement = select(CaseContext).where(
        or_(
            CaseContext.owner_id == "tracecat",
            CaseContext.owner_id == role.user_id,
        )
    )
    actions = session.exec(statement).all()
    return actions


@router.post("", tags=["cases"])
def create_case_context(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CaseContextParams,
    session: Session = Depends(get_session),
) -> CaseContext:
    """Create a new case context."""
    case_context = CaseContext(owner_id=role.user_id, **params.model_dump())
    session.add(case_context)
    session.commit()
    session.refresh(case_context)
    return params


@router.delete("/{case_context_id}", tags=["cases"])
def delete_case_context(
    role: Annotated[Role, Depends(authenticate_user)],
    case_context_id: str,
    session: Session = Depends(get_session),
):
    """Delete a case context."""
    statement = select(CaseContext).where(
        CaseContext.owner_id == role.user_id,
        CaseContext.id == case_context_id,
    )
    result = session.exec(statement)
    try:
        action = result.one()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    session.delete(action)
    session.delete(action)
    session.commit()
