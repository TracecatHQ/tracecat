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


class _Session:
    def __init__(self, org_ids: list[uuid.UUID]) -> None:
        self._org_ids = org_ids

    async def execute(self, _stmt) -> _Result:
        return _Result(self._org_ids)


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
