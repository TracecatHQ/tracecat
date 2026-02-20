from fastapi import APIRouter, HTTPException, Query, status
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.auth.schemas import UserRead
from tracecat.auth.types import Role
from tracecat.authz.controls import has_any_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import User

router = APIRouter(prefix="/users")


@router.get("/search", tags=["users"])
async def search_user(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
    ),
    email: EmailStr | None = Query(None),
    session: AsyncDBSession,
) -> UserRead:
    """Search for a user by email."""
    # Platform admin or users with org/workspace member read scope
    if not (
        role.is_privileged
        or has_any_scope(
            role.scopes or frozenset(),
            {"org:member:read", "workspace:member:read"},
        )
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide a search query",
        )

    statement = select(User)
    if email is not None:
        statement = statement.where(User.email == email)  # pyright: ignore[reportArgumentType]

    result = await session.execute(statement)
    try:
        user = result.scalar_one()
        return UserRead.model_validate(user)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
