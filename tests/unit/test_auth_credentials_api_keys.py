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
    )

    assert role is not None
    assert role.type == "service_account"
    assert role.service_account_id == service_account.id
    assert role.organization_id == organization.id
    assert role.workspace_id == workspace.id
    assert role.bound_workspace_id is None
    assert role.scopes == frozenset({"org:read", "workflow:read"})


@pytest.mark.anyio
async def test_authenticate_org_service_account_key_rejects_workspace_outside_org(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(name="Source org", slug="source-org", is_active=True)
    other_organization = Organization(
        name="Other org", slug="other-org", is_active=True
    )
    session.add_all([organization, other_organization])
    await session.flush()

    workspace = Workspace(name="Other workspace", organization_id=other_organization.id)
    session.add(workspace)
    await session.flush()

    await seed_system_scopes(session)
    scopes = await _get_scopes(session, "workflow:read")
    generated = generate_managed_api_key(prefix="tc_org_sk_")
    service_account = ServiceAccount(
        organization_id=organization.id,
        name="Cross-org automation",
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
        AsyncMock(return_value=other_organization.id),
    )

    with pytest.raises(HTTPException, match="Forbidden") as exc_info:
        await _authenticate_api_key(
            api_key=generated.raw,
            workspace_id=workspace.id,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_workspace_service_account_key_resolves_bound_workspace_for_org_route(
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
    scopes = await _get_scopes(session, "workspace:read")
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

    role = await _authenticate_api_key(
        api_key=generated.raw,
        workspace_id=None,
    )

    assert role is not None
    assert role.type == "service_account"
    assert role.service_account_id == service_account.id
    assert role.organization_id == organization.id
    assert role.workspace_id == workspace.id
    assert role.bound_workspace_id == workspace.id
    assert role.scopes == frozenset({"workspace:read"})


@pytest.mark.anyio
async def test_workspace_service_account_key_for_workspace_route_tracks_target_and_bound(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        name="Workspace target org",
        slug="workspace-target-org",
        is_active=True,
    )
    session.add(organization)
    await session.flush()
    workspace = Workspace(
        name="Workspace target workspace",
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
        name="Workspace target automation",
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

    role = await _authenticate_api_key(
        api_key=generated.raw,
        workspace_id=workspace.id,
    )

    assert role is not None
    assert role.workspace_id == workspace.id
    assert role.bound_workspace_id == workspace.id


@pytest.mark.anyio
async def test_workspace_service_account_key_rejects_mismatched_workspace_target(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        name="Workspace mismatch org",
        slug="workspace-mismatch-org",
        is_active=True,
    )
    session.add(organization)
    await session.flush()
    bound_workspace = Workspace(
        name="Bound workspace",
        organization_id=organization.id,
    )
    other_workspace = Workspace(
        name="Other workspace",
        organization_id=organization.id,
    )
    session.add_all([bound_workspace, other_workspace])
    await session.flush()

    await seed_system_scopes(session)
    scopes = await _get_scopes(session, "workflow:read")
    generated = generate_managed_api_key(prefix="tc_ws_sk_")
    service_account = ServiceAccount(
        organization_id=organization.id,
        workspace_id=bound_workspace.id,
        name="Workspace mismatch automation",
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
            workspace_id=other_workspace.id,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_workspace_service_account_key_rejects_bound_workspace_outside_org(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        name="Service account org", slug="sa-org", is_active=True
    )
    other_organization = Organization(
        name="Workspace owner org",
        slug="workspace-owner-org",
        is_active=True,
    )
    session.add_all([organization, other_organization])
    await session.flush()
    bound_workspace = Workspace(
        name="Cross-org bound workspace",
        organization_id=other_organization.id,
    )
    session.add(bound_workspace)
    await session.flush()

    await seed_system_scopes(session)
    scopes = await _get_scopes(session, "workspace:read")
    generated = generate_managed_api_key(prefix="tc_ws_sk_")
    service_account = ServiceAccount(
        organization_id=organization.id,
        workspace_id=bound_workspace.id,
        name="Cross-org bound automation",
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
        )

    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_workspace_service_account_key_can_bind_workspace_for_actor_org_route(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = Organization(
        name="Workspace actor org",
        slug="workspace-actor-org",
        is_active=True,
    )
    session.add(organization)
    await session.flush()
    workspace = Workspace(
        name="Workspace actor workspace",
        organization_id=organization.id,
    )
    session.add(workspace)
    await session.flush()

    await seed_system_scopes(session)
    scopes = await _get_scopes(session, "workspace:read")
    generated = generate_managed_api_key(prefix="tc_ws_sk_")
    service_account = ServiceAccount(
        organization_id=organization.id,
        workspace_id=workspace.id,
        name="Workspace actor automation",
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

    role = await _authenticate_api_key(
        api_key=generated.raw,
        workspace_id=None,
    )

    assert role is not None
    assert role.type == "service_account"
    assert role.organization_id == organization.id
    assert role.workspace_id == workspace.id
    assert role.scopes == frozenset({"workspace:read"})
