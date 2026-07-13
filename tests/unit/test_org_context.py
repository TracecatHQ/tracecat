"""Tests for shared active organization resolution helpers."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.org_context import (
    ACTIVE_ORG_COOKIE,
    parse_active_org_cookie,
    resolve_active_org_id,
)
from tracecat.auth.schemas import UserRole
from tracecat.db.models import Organization, OrganizationMembership, User


def _request_with_cookie(value: str | None) -> Request:
    request = MagicMock(spec=Request)
    request.cookies = {ACTIVE_ORG_COOKIE: value} if value is not None else {}
    return request


async def _seed_user(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"org-context-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
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
async def test_preferred_active_membership_is_honored(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    org_a = await _seed_org(session, "preferred-a")
    org_b = await _seed_org(session, "preferred-b")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)
    await _add_membership(session, user_id=user.id, organization_id=org_b.id)

    resolved = await resolve_active_org_id(session, user.id, preferred_org_id=org_b.id)

    assert resolved == org_b.id


@pytest.mark.anyio
async def test_nonmember_preference_falls_back_to_oldest_active_membership(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    newer_org = await _seed_org(
        session, "nonmember-newer", created_at=base_time + timedelta(days=1)
    )
    oldest_org = await _seed_org(session, "nonmember-oldest", created_at=base_time)
    other_org = await _seed_org(session, "nonmember-other")
    await _add_membership(session, user_id=user.id, organization_id=newer_org.id)
    await _add_membership(session, user_id=user.id, organization_id=oldest_org.id)

    resolved = await resolve_active_org_id(
        session, user.id, preferred_org_id=other_org.id
    )

    assert resolved == oldest_org.id


@pytest.mark.anyio
async def test_inactive_preference_falls_back_to_oldest_active_membership(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    active_org = await _seed_org(session, "inactive-preference-active")
    inactive_org = await _seed_org(
        session, "inactive-preference-inactive", is_active=False
    )
    await _add_membership(session, user_id=user.id, organization_id=active_org.id)
    await _add_membership(session, user_id=user.id, organization_id=inactive_org.id)

    resolved = await resolve_active_org_id(
        session, user.id, preferred_org_id=inactive_org.id
    )

    assert resolved == active_org.id


@pytest.mark.anyio
async def test_no_preference_single_active_membership_returns_it(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    org = await _seed_org(session, "single")
    await _add_membership(session, user_id=user.id, organization_id=org.id)

    resolved = await resolve_active_org_id(session, user.id)

    assert resolved == org.id


@pytest.mark.anyio
async def test_no_preference_multiple_active_memberships_returns_oldest(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    newer_org = await _seed_org(
        session, "multiple-newer", created_at=base_time + timedelta(days=1)
    )
    oldest_org = await _seed_org(session, "multiple-oldest", created_at=base_time)
    await _add_membership(session, user_id=user.id, organization_id=newer_org.id)
    await _add_membership(session, user_id=user.id, organization_id=oldest_org.id)

    resolved = await resolve_active_org_id(session, user.id)

    assert resolved == oldest_org.id


@pytest.mark.anyio
async def test_no_preference_identical_creation_times_returns_lower_id(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session)
    shared_time = datetime(2024, 1, 1, tzinfo=UTC)
    higher_id_org = await _seed_org(
        session,
        "higher-id",
        created_at=shared_time,
        org_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
    )
    lower_id_org = await _seed_org(
        session,
        "lower-id",
        created_at=shared_time,
        org_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
    )
    await _add_membership(session, user_id=user.id, organization_id=higher_id_org.id)
    await _add_membership(session, user_id=user.id, organization_id=lower_id_org.id)

    resolved = await resolve_active_org_id(session, user.id)

    assert resolved == lower_id_org.id


@pytest.mark.anyio
async def test_fallback_skips_older_inactive_org(session: AsyncSession) -> None:
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    inactive_org = await _seed_org(
        session, "older-inactive", created_at=base_time, is_active=False
    )
    active_org = await _seed_org(
        session, "newer-active", created_at=base_time + timedelta(days=1)
    )
    await _add_membership(session, user_id=user.id, organization_id=inactive_org.id)
    await _add_membership(session, user_id=user.id, organization_id=active_org.id)

    resolved = await resolve_active_org_id(session, user.id)

    assert resolved == active_org.id


@pytest.mark.anyio
async def test_zero_memberships_returns_none(session: AsyncSession) -> None:
    user = await _seed_user(session)

    resolved = await resolve_active_org_id(session, user.id)

    assert resolved is None


@pytest.mark.anyio
async def test_only_inactive_membership_returns_none(session: AsyncSession) -> None:
    user = await _seed_user(session)
    inactive_org = await _seed_org(session, "only-inactive", is_active=False)
    await _add_membership(session, user_id=user.id, organization_id=inactive_org.id)

    resolved = await resolve_active_org_id(session, user.id)

    assert resolved is None


def test_parse_active_org_cookie_returns_valid_uuid() -> None:
    org_id = uuid.uuid4()

    assert parse_active_org_cookie(_request_with_cookie(str(org_id))) == org_id


def test_parse_active_org_cookie_returns_none_for_malformed_value() -> None:
    assert parse_active_org_cookie(_request_with_cookie("not-a-uuid")) is None


def test_parse_active_org_cookie_returns_none_when_missing() -> None:
    assert parse_active_org_cookie(_request_with_cookie(None)) is None
