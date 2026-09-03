"""Tests for the tracecat:active-org-id cookie honored by _resolve_org_for_regular_user."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.credentials import (
    ACTIVE_ORG_COOKIE,
    _resolve_org_for_regular_user,
)
from tracecat.auth.schemas import UserRole
from tracecat.db.models import (
    Organization,
    OrganizationMembership,
    User,
)


def _request_with_cookie(value: str | None) -> Request:
    request = MagicMock(spec=Request)
    request.cookies = {ACTIVE_ORG_COOKIE: value} if value is not None else {}
    return request


async def _seed_user(session: AsyncSession, *, is_superuser: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"active-org-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_org(
    session: AsyncSession,
    slug_prefix: str,
    created_at: datetime | None = None,
    org_id: uuid.UUID | None = None,
    is_active: bool = True,
) -> Organization:
    org = Organization(
        id=org_id or uuid.uuid4(),
        name=f"Org {slug_prefix}",
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    if created_at is not None:
        org.created_at = created_at
    session.add(org)
    await session.flush()
    return org


async def _add_membership(
    session: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
) -> None:
    session.add(
        OrganizationMembership(user_id=user_id, organization_id=organization_id)
    )
    await session.flush()


@pytest.mark.anyio
async def test_cookie_honored_when_user_is_member(session: AsyncSession) -> None:
    user = await _seed_user(session)
    org_a = await _seed_org(session, "a")
    org_b = await _seed_org(session, "b")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)
    await _add_membership(session, user_id=user.id, organization_id=org_b.id)

    request = _request_with_cookie(str(org_b.id))
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == org_b.id


@pytest.mark.anyio
async def test_cookie_ignored_when_user_is_not_member(session: AsyncSession) -> None:
    """Untrusted cookie must not grant access to an org the user does not belong to."""
    user = await _seed_user(session)
    org_a = await _seed_org(session, "a")
    other_org = await _seed_org(session, "other")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)

    request = _request_with_cookie(str(other_org.id))
    resolved = await _resolve_org_for_regular_user(request, session, user)
    # Falls through to the user's only real membership.
    assert resolved == org_a.id


@pytest.mark.anyio
async def test_cookie_ignored_when_user_is_not_member_multi_org_uses_valid_membership(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    org_a = await _seed_org(session, "a", created_at=base_time + timedelta(days=1))
    org_b = await _seed_org(session, "b", created_at=base_time)
    other_org = await _seed_org(session, "other")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)
    await _add_membership(session, user_id=user.id, organization_id=org_b.id)

    request = _request_with_cookie(str(other_org.id))
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == org_b.id
    assert resolved != other_org.id


@pytest.mark.anyio
async def test_cookie_ignored_when_org_is_inactive(session: AsyncSession) -> None:
    """A cookie pointing at an inactive org falls through to an active membership."""
    user = await _seed_user(session)
    active_org = await _seed_org(session, "active")
    inactive_org = await _seed_org(session, "inactive", is_active=False)
    await _add_membership(session, user_id=user.id, organization_id=active_org.id)
    await _add_membership(session, user_id=user.id, organization_id=inactive_org.id)

    request = _request_with_cookie(str(inactive_org.id))
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == active_org.id


@pytest.mark.anyio
async def test_cookie_ignored_for_garbage_value(session: AsyncSession) -> None:
    user = await _seed_user(session)
    org_a = await _seed_org(session, "a")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)

    request = _request_with_cookie("not-a-uuid")
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == org_a.id


@pytest.mark.anyio
async def test_cookie_ignored_for_superuser_who_is_not_member(
    session: AsyncSession,
) -> None:
    """The superuser flag must NOT grant access to an org without explicit membership."""
    user = await _seed_user(session, is_superuser=True)
    org_a = await _seed_org(session, "a")
    other_org = await _seed_org(session, "other")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)

    request = _request_with_cookie(str(other_org.id))
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == org_a.id


@pytest.mark.anyio
async def test_no_cookie_single_membership_returns_it(session: AsyncSession) -> None:
    user = await _seed_user(session)
    org_a = await _seed_org(session, "a")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)

    request = _request_with_cookie(None)
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == org_a.id


@pytest.mark.anyio
async def test_no_cookie_multi_membership_uses_stable_membership(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    org_a = await _seed_org(session, "a", created_at=base_time + timedelta(days=1))
    org_b = await _seed_org(session, "b", created_at=base_time)
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)
    await _add_membership(session, user_id=user.id, organization_id=org_b.id)

    request = _request_with_cookie(None)
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == org_b.id


@pytest.mark.anyio
async def test_no_cookie_multi_membership_uses_stable_id_tiebreaker(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    shared_time = datetime(2024, 1, 1, tzinfo=UTC)
    org_a = await _seed_org(
        session,
        "a",
        created_at=shared_time,
        org_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
    )
    org_b = await _seed_org(
        session,
        "b",
        created_at=shared_time,
        org_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
    )
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)
    await _add_membership(session, user_id=user.id, organization_id=org_b.id)

    request = _request_with_cookie(None)
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == org_b.id


@pytest.mark.anyio
async def test_no_cookie_fallback_skips_inactive_orgs(session: AsyncSession) -> None:
    """The stable fallback only considers active orgs, even older ones."""
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    inactive_org = await _seed_org(
        session, "inactive", created_at=base_time, is_active=False
    )
    active_org = await _seed_org(
        session, "active", created_at=base_time + timedelta(days=1)
    )
    await _add_membership(session, user_id=user.id, organization_id=inactive_org.id)
    await _add_membership(session, user_id=user.id, organization_id=active_org.id)

    request = _request_with_cookie(None)
    resolved = await _resolve_org_for_regular_user(request, session, user)
    assert resolved == active_org.id


@pytest.mark.anyio
async def test_no_cookie_only_inactive_memberships_raises_400(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    inactive_org = await _seed_org(session, "inactive", is_active=False)
    await _add_membership(session, user_id=user.id, organization_id=inactive_org.id)

    request = _request_with_cookie(None)
    with pytest.raises(HTTPException) as exc:
        await _resolve_org_for_regular_user(request, session, user)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "no organization memberships" in exc.value.detail


@pytest.mark.anyio
async def test_no_cookie_no_memberships_raises_400(session: AsyncSession) -> None:
    user = await _seed_user(session)

    request = _request_with_cookie(None)
    with pytest.raises(HTTPException) as exc:
        await _resolve_org_for_regular_user(request, session, user)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "no organization memberships" in exc.value.detail
