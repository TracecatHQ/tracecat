from fastapi import APIRouter, HTTPException, Query, status
from pydantic import EmailStr
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.schemas import UserRead
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import User

router = APIRouter(
    prefix="/internal/users", tags=["internal-users"], include_in_schema=False
)


@router.get("/search")
async def executor_search_user(
    *,
    role: ExecutorWorkspaceRole,
    email: EmailStr | None = Query(None),
    session: AsyncDBSession,
) -> UserRead:
    _ = role
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide a search query",
        )

    statement = select(User).where(User.email == email)  # pyright: ignore[reportArgumentType]
    result = await session.execute(statement)
    try:
        user = result.scalar_one()
        return UserRead.model_validate(user)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
