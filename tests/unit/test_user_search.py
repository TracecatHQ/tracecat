"""Tests for the org-scoped user email search route."""

import uuid

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.router import search_user
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.db.models import Organization, OrganizationMembership, User

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


async def _org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Test Org",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.flush()
    return org


async def _member(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        first_name="Test",
        last_name="User",
        settings={"theme": "dark"},
    )
    session.add(user)
    await session.flush()
    session.add(OrganizationMembership(user_id=user.id, organization_id=org.id))
    await session.flush()
    return user


def _role(org: Organization, *, is_platform_superuser: bool = False) -> Role:
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({"org:member:read"}),
        is_platform_superuser=is_platform_superuser,
    )


async def _search_status(session: AsyncSession, role: Role, email: str) -> int:
    with pytest.raises(HTTPException) as exc_info:
        await search_user(role=role, email=email, session=session)
    return exc_info.value.status_code


async def test_search_returns_minimal_fields_for_own_org_member(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    user = await _member(session, org)

    result = await search_user(role=_role(org), email=user.email, session=session)

    assert result.model_dump() == {
        "id": user.id,
        "email": user.email,
        "first_name": "Test",
        "last_name": "User",
    }


async def test_search_hides_users_outside_caller_org(session: AsyncSession) -> None:
    """A user only in another org is indistinguishable from an unknown email."""
    org_a = await _org(session)
    org_b = await _org(session)
    other_org_user = await _member(session, org_b)

    role = _role(org_a)
    assert await _search_status(session, role, other_org_user.email) == (
        status.HTTP_404_NOT_FOUND
    )
    assert await _search_status(session, role, "nobody@example.com") == (
        status.HTTP_404_NOT_FOUND
    )


async def test_search_scopes_platform_superuser_to_active_org(
    session: AsyncSession,
) -> None:
    """Platform admins look up users across orgs through /admin/users instead."""
    org_a = await _org(session)
    org_b = await _org(session)
    other_org_user = await _member(session, org_b)

    role = _role(org_a, is_platform_superuser=True)
    assert role.is_privileged
    assert await _search_status(session, role, other_org_user.email) == (
        status.HTTP_404_NOT_FOUND
    )


async def test_search_without_org_context_returns_not_found(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    user = await _member(session, org)
    role = Role(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        is_platform_superuser=True,
    )

    assert await _search_status(session, role, user.email) == (
        status.HTTP_404_NOT_FOUND
    )
