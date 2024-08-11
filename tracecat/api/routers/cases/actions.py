from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.auth.credentials import authenticate_user_for_workspace
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import CaseAction
from tracecat.types.api import CaseActionParams
from tracecat.types.auth import Role

router = APIRouter(prefix="/case-actions")


@router.get("", tags=["cases"])
async def list_case_actions(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    session: AsyncSession = Depends(get_async_session),
) -> list[CaseAction]:
    """List all case actions."""
    statement = select(CaseAction).where(
        or_(
            CaseAction.owner_id == config.TRACECAT__DEFAULT_USER_ID,
            CaseAction.owner_id == role.workspace_id,
        )
    )
    result = await session.exec(statement)
    actions = result.all()
    return actions


@router.post("", tags=["cases"])
async def create_case_action(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: CaseActionParams,
    session: AsyncSession = Depends(get_async_session),
) -> CaseAction:
    """Create a new case action."""
    case_action = CaseAction(owner_id=role.workspace_id, **params.model_dump())
    session.add(case_action)
    await session.commit()
    await session.refresh(case_action)
    return case_action


@router.delete("/{case_action_id}", tags=["cases"])
async def delete_case_action(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    case_action_id: str,
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a case action."""
    statement = select(CaseAction).where(
        CaseAction.owner_id == role.workspace_id,
        CaseAction.id == case_action_id,
    )
    result = await session.exec(statement)
    try:
        action = result.one()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    await session.delete(action)
    await session.commit()
