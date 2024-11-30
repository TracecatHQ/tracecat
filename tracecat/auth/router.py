from fastapi import APIRouter, HTTPException, Query, status
from pydantic import EmailStr
from sqlalchemy.exc import NoResultFound
from sqlmodel import select

from tracecat.auth.credentials import RoleACL
from tracecat.auth.models import UserRead
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import User
from tracecat.types.auth import AccessLevel, Role

router = APIRouter(prefix="/users")


@router.get("/search", tags=["users"])
async def search_user(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    email: EmailStr | None = Query(None),
    session: AsyncDBSession,
) -> UserRead:
    """Create new user."""
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide a search query",
        )

    statement = select(User)
    if email is not None:
        statement = statement.where(User.email == email)

    result = await session.exec(statement)
    try:
        user = result.one()
        return UserRead.model_validate(user)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
