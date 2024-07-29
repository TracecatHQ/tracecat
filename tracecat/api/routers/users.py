from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.auth.credentials import authenticate_user
from tracecat.db.engine import get_session
from tracecat.db.schemas import User
from tracecat.types.api import UpdateUserParams
from tracecat.types.auth import Role

router = APIRouter(prefix="/users")


@router.post("", status_code=status.HTTP_201_CREATED, tags=["users"])
def create_user(
    role: Annotated[Role, Depends(authenticate_user)],
    session: Session = Depends(get_session),
) -> User:
    """Create new user."""

    # Check if user exists
    statement = select(User).where(User.id == role.user_id).limit(1)
    result = session.exec(statement)

    user = result.one_or_none()
    if user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="User already exists"
        )
    user = User(owner_id="tracecat", id=role.user_id)

    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.get("", tags=["users"])
def get_user(
    role: Annotated[Role, Depends(authenticate_user)],
    session: Session = Depends(get_session),
) -> User:
    """Get a user."""

    # Get user given user_id
    statement = select(User).where(User.id == role.user_id)
    result = session.exec(statement)
    try:
        user = result.one()
        return user
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e


@router.post("", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def update_user(
    role: Annotated[Role, Depends(authenticate_user)],
    params: UpdateUserParams,
    session: Session = Depends(get_session),
) -> None:
    """Update a user."""

    statement = select(User).where(User.id == role.user_id)
    result = session.exec(statement)
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
    session.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def delete_user(
    role: Annotated[Role, Depends(authenticate_user)],
    session: Session = Depends(get_session),
) -> None:
    """Delete a user."""

    statement = select(User).where(User.id == role.user_id)
    result = session.exec(statement)
    try:
        user = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e
    session.delete(user)
    session.commit()
