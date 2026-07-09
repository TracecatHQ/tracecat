from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from tracecat.auth.credentials import ACTIVE_ORG_COOKIE
from tracecat.db.models import User
from tracecat.mcp.oidc import session as oidc_session


def _request_with_cookie(value: str | None) -> Request:
    request = MagicMock(spec=Request)
    request.cookies = {ACTIVE_ORG_COOKIE: value} if value is not None else {}
    return request


class _Result:
    def __init__(self, org_ids: list[uuid.UUID]) -> None:
        self._org_ids = org_ids

    def all(self) -> list[tuple[uuid.UUID]]:
        return [(org_id,) for org_id in self._org_ids]

    def scalar_one_or_none(self) -> uuid.UUID | None:
        return self._org_ids[0] if self._org_ids else None

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._org_ids)


class _ScalarResult:
    def __init__(self, org_ids: list[uuid.UUID]) -> None:
        self._org_ids = org_ids

    def all(self) -> list[uuid.UUID]:
        return self._org_ids


class _Session:
    def __init__(self, org_ids: list[uuid.UUID], *additional: list[uuid.UUID]) -> None:
        self._responses = [org_ids, *additional]

    async def execute(self, _stmt) -> _Result:
        return _Result(self._responses.pop(0))


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

    with pytest.raises(ValueError, match="expected exactly 1 active organization"):
        await oidc_session.resolve_authorize_session(user)


@pytest.mark.anyio
async def test_resolve_authorize_session_honors_active_org_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_b = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com", is_superuser=False),
    )

    monkeypatch.setattr(
        oidc_session,
        "get_async_session_bypass_rls_context_manager",
        lambda: _AsyncContext(_Session([org_b])),
    )

    result = await oidc_session.resolve_authorize_session(
        user,
        request=_request_with_cookie(str(org_b)),
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == org_b


@pytest.mark.anyio
async def test_resolve_authorize_session_multi_org_without_cookie_uses_stable_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    older_org = uuid.uuid4()
    newer_org = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com", is_superuser=False),
    )

    monkeypatch.setattr(
        oidc_session,
        "get_async_session_bypass_rls_context_manager",
        lambda: _AsyncContext(_Session([older_org, newer_org])),
    )

    result = await oidc_session.resolve_authorize_session(
        user,
        request=_request_with_cookie(None),
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == older_org


@pytest.mark.anyio
async def test_resolve_authorize_session_ignores_inactive_active_org_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_org = uuid.uuid4()
    inactive_org = uuid.uuid4()
    user = cast(
        User,
        SimpleNamespace(id=uuid.uuid4(), email="user@example.com", is_superuser=False),
    )

    monkeypatch.setattr(
        oidc_session,
        "get_async_session_bypass_rls_context_manager",
        lambda: _AsyncContext(_Session([], [active_org])),
    )

    result = await oidc_session.resolve_authorize_session(
        user,
        request=_request_with_cookie(str(inactive_org)),
    )

    assert isinstance(result, oidc_session.SessionResult)
    assert result.organization_id == active_org
