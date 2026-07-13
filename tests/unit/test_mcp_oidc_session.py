"""Tests for MCP OIDC session and active organization resolution."""

import uuid
from datetime import UTC, datetime, timedelta
from types import TracebackType
from unittest.mock import MagicMock

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.org_context import ACTIVE_ORG_COOKIE
from tracecat.auth.schemas import UserRole
from tracecat.db.models import Organization, OrganizationMembership, User
from tracecat.mcp.oidc import session as oidc_session


class _AsyncSessionContext:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def _request_with_cookie(value: str | None) -> Request:
    request = MagicMock(spec=Request)
    request.cookies = {ACTIVE_ORG_COOKIE: value} if value is not None else {}
    return request


async def _seed_user(session: AsyncSession, *, is_superuser: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"mcp-oidc-session-{uuid.uuid4().hex[:8]}@example.com",
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
    is_active: bool = True,
) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
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


def _patch_session(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    monkeypatch.setattr(
        oidc_session,
        "get_async_session_bypass_rls_context_manager",
        lambda: _AsyncSessionContext(session),
    )


@pytest.mark.anyio
async def test_resolve_authorize_session_without_user_requires_login() -> None:
    result = await oidc_session.resolve_authorize_session(
        None, _request_with_cookie(None)
    )

    assert isinstance(result, oidc_session.SessionNeedsAction)
    assert result.action == oidc_session.NeedsAction.LOGIN


@pytest.mark.anyio
async def test_resolve_authorize_session_honors_active_org_cookie(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session)
    org_a = await _seed_org(session, "cookie-a")
    org_b = await _seed_org(session, "cookie-b")
    await _add_membership(session, user_id=user.id, organization_id=org_a.id)
    await _add_membership(session, user_id=user.id, organization_id=org_b.id)
    _patch_session(monkeypatch, session)

    result = await oidc_session.resolve_authorize_session(
        user, _request_with_cookie(str(org_b.id))
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == org_b.id


@pytest.mark.anyio
async def test_resolve_authorize_session_ignores_nonmember_cookie(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session)
    member_org = await _seed_org(session, "member")
    other_org = await _seed_org(session, "other")
    await _add_membership(session, user_id=user.id, organization_id=member_org.id)
    _patch_session(monkeypatch, session)

    result = await oidc_session.resolve_authorize_session(
        user, _request_with_cookie(str(other_org.id))
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == member_org.id


@pytest.mark.anyio
async def test_resolve_authorize_session_ignores_malformed_cookie(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    newer_org = await _seed_org(
        session, "malformed-newer", created_at=base_time + timedelta(days=1)
    )
    oldest_org = await _seed_org(session, "malformed-oldest", created_at=base_time)
    await _add_membership(session, user_id=user.id, organization_id=newer_org.id)
    await _add_membership(session, user_id=user.id, organization_id=oldest_org.id)
    _patch_session(monkeypatch, session)

    result = await oidc_session.resolve_authorize_session(
        user, _request_with_cookie("not-a-uuid")
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == oldest_org.id


@pytest.mark.anyio
async def test_resolve_authorize_session_single_membership(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session)
    org = await _seed_org(session, "single")
    await _add_membership(session, user_id=user.id, organization_id=org.id)
    _patch_session(monkeypatch, session)

    result = await oidc_session.resolve_authorize_session(
        user, _request_with_cookie(None)
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == org.id


@pytest.mark.anyio
async def test_resolve_authorize_session_multiple_memberships_returns_oldest(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session)
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    newer_org = await _seed_org(
        session, "multiple-newer", created_at=base_time + timedelta(days=1)
    )
    oldest_org = await _seed_org(session, "multiple-oldest", created_at=base_time)
    await _add_membership(session, user_id=user.id, organization_id=newer_org.id)
    await _add_membership(session, user_id=user.id, organization_id=oldest_org.id)
    _patch_session(monkeypatch, session)

    result = await oidc_session.resolve_authorize_session(
        user, _request_with_cookie(None)
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == oldest_org.id


@pytest.mark.anyio
async def test_resolve_authorize_session_without_memberships_errors(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session)
    _patch_session(monkeypatch, session)

    with pytest.raises(
        oidc_session.OrgResolutionError,
        match="no active organization membership",
    ) as exc_info:
        await oidc_session.resolve_authorize_session(user, _request_with_cookie(None))

    assert exc_info.value.membership_count == 0


@pytest.mark.anyio
async def test_resolve_authorize_session_with_only_inactive_membership_errors(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session)
    inactive_org = await _seed_org(session, "inactive", is_active=False)
    await _add_membership(session, user_id=user.id, organization_id=inactive_org.id)
    _patch_session(monkeypatch, session)

    with pytest.raises(
        oidc_session.OrgResolutionError,
        match="no active organization membership",
    ) as exc_info:
        await oidc_session.resolve_authorize_session(user, _request_with_cookie(None))

    assert exc_info.value.membership_count == 0


@pytest.mark.anyio
async def test_resolve_authorize_session_superuser_without_membership_errors(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _seed_user(session, is_superuser=True)
    _patch_session(monkeypatch, session)

    with pytest.raises(
        oidc_session.OrgResolutionError,
        match="no active organization membership",
    ) as exc_info:
        await oidc_session.resolve_authorize_session(user, _request_with_cookie(None))

    assert exc_info.value.membership_count == 0
