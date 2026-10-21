from fastapi import APIRouter, HTTPException, Query, status
from pydantic import EmailStr
from sqlalchemy import select

from tracecat.auth.credentials import RoleACL
from tracecat.auth.schemas import UserSearchRead
from tracecat.auth.types import Role
from tracecat.authz.controls import has_any_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationMembership, User

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
) -> UserSearchRead:
    """Find a member of the caller's organization by email.

    Users outside the caller's organization return 404, the same as an unknown
    email, so the route cannot be used to enumerate accounts. Platform admins
    are scoped the same way; platform-wide lookups go through ``/admin/users``.
    """
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

    not_found = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
    )
    if role.organization_id is None:
        raise not_found

    statement = (
        select(User)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(
            User.email == email,  # pyright: ignore[reportArgumentType]
            OrganizationMembership.organization_id == role.organization_id,
        )
    )
    user = (await session.execute(statement)).scalar_one_or_none()
    if user is None:
        raise not_found
    return UserSearchRead.model_validate(user, from_attributes=True)
