from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from tracecat.auth.api_keys import generate_managed_api_key
from tracecat.auth.credentials import _authenticate_api_key
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.models import (
    Organization,
    Scope,
    ServiceAccount,
    ServiceAccountApiKey,
    Workspace,
)


async def _get_scopes(session, *names: str) -> list[Scope]:
    result = await session.execute(select(Scope).where(Scope.name.in_(names)))
    return list(result.scalars().all())


@pytest.mark.anyio
async def test_authenticate_org_service_account_key_for_workspace_route(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        name="Auth test org", slug="auth-test-org", is_active=True
    )
    session.add(organization)
    await session.flush()
    workspace = Workspace(name="Auth test workspace", organization_id=organization.id)
    session.add(workspace)
    await session.flush()

    await seed_system_scopes(session)
    scopes = await _get_scopes(session, "org:read", "workflow:read")
    generated = generate_managed_api_key(prefix="tc_org_sk_")
    service_account = ServiceAccount(
        organization_id=organization.id,
        name="Org automation",
        scopes=scopes,
    )
    session.add(service_account)
    await session.flush()
    session.add(
        ServiceAccountApiKey(
            service_account_id=service_account.id,
            name="Primary",
            key_id=generated.key_id,
            hashed=generated.hashed,
            salt=generated.salt_b64,
            preview=generated.preview(),
        )
    )
    await session.commit()

    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(
        "tracecat.auth.credentials.get_async_session_bypass_rls_context_manager",
        _session_cm,
    )
    monkeypatch.setattr(
        "tracecat.auth.credentials._get_workspace_org_id",
        AsyncMock(return_value=organization.id),
    )

    role = await _authenticate_api_key(
        api_key=generated.raw,
        workspace_id=workspace.id,
        require_workspace="yes",
    )

    assert role is not None
    assert role.type == "service_account"
    assert role.service_account_id == service_account.id
    assert role.organization_id == organization.id
    assert role.workspace_id == workspace.id
    assert role.scopes == frozenset({"org:read", "workflow:read"})


@pytest.mark.anyio
async def test_workspace_service_account_key_rejects_org_route(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        name="Workspace key org",
        slug="workspace-key-org",
        is_active=True,
    )
    session.add(organization)
    await session.flush()
    workspace = Workspace(
        name="Workspace key workspace",
        organization_id=organization.id,
    )
    session.add(workspace)
    await session.flush()

    await seed_system_scopes(session)
    scopes = await _get_scopes(session, "workflow:read")
    generated = generate_managed_api_key(prefix="tc_ws_sk_")
    service_account = ServiceAccount(
        organization_id=organization.id,
        workspace_id=workspace.id,
        name="Workspace automation",
        scopes=scopes,
    )
    session.add(service_account)
    await session.flush()
    session.add(
        ServiceAccountApiKey(
            service_account_id=service_account.id,
            name="Primary",
            key_id=generated.key_id,
            hashed=generated.hashed,
            salt=generated.salt_b64,
            preview=generated.preview(),
        )
    )
    await session.commit()

    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(
        "tracecat.auth.credentials.get_async_session_bypass_rls_context_manager",
        _session_cm,
    )

    with pytest.raises(HTTPException, match="Forbidden") as exc_info:
        await _authenticate_api_key(
            api_key=generated.raw,
            workspace_id=None,
            require_workspace="no",
        )

    assert exc_info.value.status_code == 403
