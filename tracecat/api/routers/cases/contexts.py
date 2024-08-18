from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.auth.credentials import authenticate_user_for_workspace
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import CaseContext
from tracecat.types.api import CaseContextParams
from tracecat.types.auth import Role

router = APIRouter(prefix="/case-contexts")


@router.get("", tags=["cases"])
async def list_case_contexts(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    session: AsyncSession = Depends(get_async_session),
) -> list[CaseContext]:
    """List all case contexts."""
    statement = select(CaseContext).where(
        or_(
            CaseContext.owner_id == config.TRACECAT__DEFAULT_USER_ID,
            CaseContext.owner_id == role.workspace_id,
        )
    )
    result = await session.exec(statement)
    case_contexts = result.all()
    return case_contexts


@router.post("", tags=["cases"])
async def create_case_context(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: CaseContextParams,
    session: AsyncSession = Depends(get_async_session),
) -> CaseContext:
    """Create a new case context."""
    case_context = CaseContext(owner_id=role.workspace_id, **params.model_dump())
    session.add(case_context)
    await session.commit()
    await session.refresh(case_context)
    return params


@router.delete("/{case_context_id}", tags=["cases"])
async def delete_case_context(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    case_context_id: str,
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a case context."""
    statement = select(CaseContext).where(
        CaseContext.owner_id == role.workspace_id,
        CaseContext.id == case_context_id,
    )
    result = await session.exec(statement)
    try:
        case_context = result.one()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    await session.delete(case_context)
    await session.commit()
