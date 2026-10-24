import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import grant_org_membership
from tracecat.auth.credentials import ACTIVE_ORG_COOKIE
from tracecat.auth.schemas import UserRole
from tracecat.db.models import Organization, User
from tracecat.mcp.oidc import session as oidc_session


def _request_with_cookie(value: str | None) -> Request:
    request = MagicMock(spec=Request)
    request.cookies = {ACTIVE_ORG_COOKIE: value} if value is not None else {}
    return request


@pytest.fixture
def use_test_session(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncSession:
    @contextlib.asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        oidc_session, "get_async_session_bypass_rls_context_manager", _ctx
    )
    return session


async def _seed_user(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"mcp-oidc-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_member_org(
    session: AsyncSession,
    user: User,
    *,
    created_at: datetime,
    is_active: bool = True,
) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Org",
        slug=f"mcp-oidc-{uuid.uuid4().hex[:8]}",
        is_active=is_active,
    )
    org.created_at = created_at
    session.add(org)
    await session.flush()
    await grant_org_membership(session, user_id=user.id, organization_id=org.id)
    return org


_BASE_TIME = datetime(2024, 1, 1, tzinfo=UTC)


@pytest.mark.anyio
async def test_resolve_authorize_session_without_user_needs_login() -> None:
    result = await oidc_session.resolve_authorize_session(
        _request_with_cookie(None), None
    )

    assert result == oidc_session.SessionNeedsAction(
        action=oidc_session.NeedsAction.LOGIN
    )


@pytest.mark.anyio
async def test_resolve_authorize_session_single_org(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)
    org = await _seed_member_org(use_test_session, user, created_at=_BASE_TIME)

    result = await oidc_session.resolve_authorize_session(
        _request_with_cookie(None), user
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.user is user
    assert result.organization_id == org.id


@pytest.mark.anyio
async def test_resolve_authorize_session_multi_org_uses_active_org_cookie(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)
    await _seed_member_org(use_test_session, user, created_at=_BASE_TIME)
    newer = await _seed_member_org(
        use_test_session, user, created_at=_BASE_TIME + timedelta(days=1)
    )

    result = await oidc_session.resolve_authorize_session(
        _request_with_cookie(str(newer.id)), user
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == newer.id


@pytest.mark.anyio
async def test_resolve_authorize_session_multi_org_without_cookie_uses_oldest(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)
    oldest = await _seed_member_org(use_test_session, user, created_at=_BASE_TIME)
    await _seed_member_org(
        use_test_session, user, created_at=_BASE_TIME + timedelta(days=1)
    )

    result = await oidc_session.resolve_authorize_session(
        _request_with_cookie(None), user
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == oldest.id


@pytest.mark.anyio
async def test_resolve_authorize_session_ignores_cookie_for_non_member_org(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)
    org = await _seed_member_org(use_test_session, user, created_at=_BASE_TIME)
    other_user = await _seed_user(use_test_session)
    other_org = await _seed_member_org(
        use_test_session, other_user, created_at=_BASE_TIME
    )

    result = await oidc_session.resolve_authorize_session(
        _request_with_cookie(str(other_org.id)), user
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == org.id


@pytest.mark.anyio
async def test_resolve_authorize_session_skips_inactive_orgs(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)
    inactive = await _seed_member_org(
        use_test_session, user, created_at=_BASE_TIME, is_active=False
    )
    active = await _seed_member_org(
        use_test_session, user, created_at=_BASE_TIME + timedelta(days=1)
    )

    result = await oidc_session.resolve_authorize_session(
        _request_with_cookie(str(inactive.id)), user
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == active.id


@pytest.mark.anyio
async def test_resolve_authorize_session_without_membership_errors(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)

    with pytest.raises(oidc_session.OrgResolutionError, match="no organization"):
        await oidc_session.resolve_authorize_session(_request_with_cookie(None), user)


@pytest.mark.anyio
async def test_resolve_authorize_session_lists_active_orgs_oldest_first(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)
    newer = await _seed_member_org(
        use_test_session, user, created_at=_BASE_TIME + timedelta(days=2)
    )
    await _seed_member_org(
        use_test_session,
        user,
        created_at=_BASE_TIME + timedelta(days=1),
        is_active=False,
    )
    oldest = await _seed_member_org(use_test_session, user, created_at=_BASE_TIME)
    other_user = await _seed_user(use_test_session)
    await _seed_member_org(use_test_session, other_user, created_at=_BASE_TIME)

    result = await oidc_session.resolve_authorize_session(
        _request_with_cookie(str(newer.id)), user
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == newer.id
    assert [org.id for org in result.organizations] == [oldest.id, newer.id]
    assert result.organizations[0].slug == oldest.slug


@pytest.mark.anyio
async def test_list_active_organizations_without_membership_is_empty(
    use_test_session: AsyncSession,
) -> None:
    user = await _seed_user(use_test_session)

    assert await oidc_session.list_active_organizations(use_test_session, user.id) == []
