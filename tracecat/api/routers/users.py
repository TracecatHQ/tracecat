from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.auth.credentials import authenticate_user_for_workspace
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import User
from tracecat.types.api import UpdateUserParams
from tracecat.types.auth import Role

router = APIRouter(prefix="/users")


@router.post("", status_code=status.HTTP_201_CREATED, tags=["users"])
async def create_user(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Create new user."""

    # Check if user exists
    statement = select(User).where(User.id == role.workspace_id).limit(1)
    result = await session.exec(statement)

    user = result.one_or_none()
    if user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="User already exists"
        )
    user = User(owner_id=config.TRACECAT__DEFAULT_USER_ID, id=role.workspace_id)

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.get("", tags=["users"])
async def get_user(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Get a user."""

    # Get user given user_id
    statement = select(User).where(User.id == role.workspace_id)
    result = await session.exec(statement)
    try:
        user = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e
    return user


@router.post("", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
async def update_user(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: UpdateUserParams,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Update a user."""

    statement = select(User).where(User.id == role.workspace_id)
    result = await session.exec(statement)
    try:
        user = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e

    if params.tier is not None:
        user.tier = params.tier
    if params.settings is not None:
        user.settings = params.settings

    session.add(user)
    await session.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
async def delete_user(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete a user."""

    statement = select(User).where(User.id == role.workspace_id)
    result = await session.exec(statement)
    try:
        user = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e
    await session.delete(user)
    await session.commit()
