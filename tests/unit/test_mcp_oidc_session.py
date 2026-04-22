from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from tracecat.db.models import User
from tracecat.mcp.oidc import session as oidc_session


@pytest.mark.anyio
async def test_resolve_superuser_org_rejects_multi_tenant_superuser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oidc_session.config, "TRACECAT__EE_MULTI_TENANT", True)

    with pytest.raises(
        ValueError,
        match="Platform superusers cannot authorize tenant MCP sessions",
    ):
        await oidc_session._resolve_superuser_org(
            cast(Request, object()),
            cast(AsyncSession, object()),
            cast(User, SimpleNamespace(id="user-id")),
        )
