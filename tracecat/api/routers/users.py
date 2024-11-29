from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.credentials import RoleACL
from tracecat.auth.models import UserRead
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import User
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role

router = APIRouter(prefix="/users")


@router.get("/search", tags=["users"])
async def search_user(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        min_access_level=AccessLevel.ADMIN,
    ),
    email: str | None = Query(None),
    session: AsyncSession = Depends(get_async_session),
) -> UserRead:
    """Create new user."""
    logger.info("HIT SEARCH")
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
