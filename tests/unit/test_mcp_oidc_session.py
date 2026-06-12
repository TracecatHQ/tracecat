import uuid
from types import SimpleNamespace
from typing import cast

import pytest

from tracecat.db.models import User
from tracecat.mcp.oidc import session as oidc_session


class _Result:
    def __init__(self, org_ids: list[uuid.UUID]) -> None:
        self._org_ids = org_ids

    def all(self) -> list[tuple[uuid.UUID]]:
        return [(org_id,) for org_id in self._org_ids]

    def scalar_one_or_none(self) -> uuid.UUID | None:
        if not self._org_ids:
            return None
        return self._org_ids[0]


class _Session:
    def __init__(self, *results: list[uuid.UUID]) -> None:
        self._results = list(results)

    async def execute(self, _stmt) -> _Result:
        if not self._results:
            return _Result([])
        return _Result(self._results.pop(0))


class _AsyncContext:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.anyio
async def test_resolve_authorize_session_superuser_uses_membership_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="admin@example.com", is_superuser=True),
    )

    monkeypatch.setattr(
        oidc_session,
        "get_async_session_bypass_rls_context_manager",
        lambda: _AsyncContext(_Session([org_id])),
    )

    result = await oidc_session.resolve_authorize_session(user)

    assert isinstance(result, oidc_session.SessionResult)
    assert result.user is user
    assert result.organization_id == org_id


@pytest.mark.anyio
async def test_resolve_authorize_session_superuser_without_membership_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="admin@example.com", is_superuser=True),
    )

    monkeypatch.setattr(
        oidc_session,
        "get_async_session_bypass_rls_context_manager",
        lambda: _AsyncContext(_Session([])),
    )

    with pytest.raises(ValueError, match="expected exactly 1 organization membership"):
        await oidc_session.resolve_authorize_session(user)


@pytest.mark.anyio
async def test_resolve_regular_user_org_uses_valid_org_hint() -> None:
    hinted_org_id = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com"),
    )

    result = await oidc_session._resolve_regular_user_org(
        cast("oidc_session.AsyncSession", _Session([hinted_org_id])),
        user,
        organization_hint="security-team",
    )

    assert result.organization_id == hinted_org_id


@pytest.mark.anyio
async def test_resolve_regular_user_org_rejects_invalid_org_hint() -> None:
    fallback_org_id = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com"),
    )

    with pytest.raises(ValueError, match="Org hint does not match"):
        await oidc_session._resolve_regular_user_org(
            cast("oidc_session.AsyncSession", _Session([], [fallback_org_id])),
            user,
            organization_hint="missing-org",
        )


@pytest.mark.anyio
async def test_resolve_regular_user_org_treats_blank_org_hint_as_absent() -> None:
    fallback_org_id = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com"),
    )

    result = await oidc_session._resolve_regular_user_org(
        cast("oidc_session.AsyncSession", _Session([fallback_org_id])),
        user,
        organization_hint="   ",
    )

    assert result.organization_id == fallback_org_id


@pytest.mark.anyio
async def test_resolve_org_hint_prefers_uuid_before_slug_lookup() -> None:
    uuid_org_id = uuid.uuid4()
    slug_org_id = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com"),
    )

    result = await oidc_session._resolve_org_hint(
        cast("oidc_session.AsyncSession", _Session([uuid_org_id], [slug_org_id])),
        user,
        str(uuid_org_id),
    )

    assert result == uuid_org_id


@pytest.mark.anyio
async def test_resolve_org_hint_rejects_ambiguous_slug_matches() -> None:
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com"),
    )

    result = await oidc_session._resolve_org_hint(
        cast(
            "oidc_session.AsyncSession",
            _Session([uuid.uuid4(), uuid.uuid4()]),
        ),
        user,
        "shared-slug",
    )

    assert result is None


@pytest.mark.anyio
async def test_resolve_regular_user_org_uses_valid_active_org_cookie() -> None:
    cookie_org_id = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com"),
    )

    result = await oidc_session._resolve_regular_user_org(
        cast("oidc_session.AsyncSession", _Session([cookie_org_id])),
        user,
        cookie_org_id=cookie_org_id,
    )

    assert result.organization_id == cookie_org_id


@pytest.mark.anyio
async def test_resolve_regular_user_org_requires_disambiguation_for_multi_org_user() -> (
    None
):
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com"),
    )

    with pytest.raises(ValueError, match="explicit org context"):
        await oidc_session._resolve_regular_user_org(
            cast("oidc_session.AsyncSession", _Session([uuid.uuid4(), uuid.uuid4()])),
            user,
        )
