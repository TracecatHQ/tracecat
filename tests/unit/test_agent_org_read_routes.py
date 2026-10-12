"""Org-level agent reads that workspace pages call.

The member floor carries no agent scopes, so a user whose roles are assigned
per workspace reaches these routes only when the request names the workspace.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import grant_workspace_membership
from tracecat.agent import router as agent_router
from tracecat.agent.provider import router as agent_provider_router
from tracecat.auth import credentials
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.seeding import seed_system_scopes
from tracecat.authz.service import query_effective_scopes
from tracecat.contexts import ctx_role
from tracecat.db.models import Organization, User, Workspace
from tracecat.exceptions import ScopeDeniedError

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("db", "auth_reads_test_session"),
]

type Endpoint = Callable[[Role, AsyncSession], Awaitable[object]]


async def _get_default_model(role: Role, session: AsyncSession) -> object:
    return await agent_router.get_default_model(role=role, session=session)


async def _get_default_model_selection(role: Role, session: AsyncSession) -> object:
    return await agent_router.get_default_model_selection(role=role, session=session)


async def _list_custom_providers(role: Role, session: AsyncSession) -> object:
    return await agent_provider_router.list_custom_providers(
        role=role, session=session, cursor=None, limit=50
    )


ENDPOINTS: list[Endpoint] = [
    _get_default_model,
    _get_default_model_selection,
    _list_custom_providers,
]


@pytest.fixture
def auth_reads_test_session(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the auth lookups that open their own session at the test session.

    The test transaction is invisible to other connections, so the cached
    lookups would otherwise see neither the workspace nor the assignments.
    """

    async def _workspace_org_id(workspace_id: uuid.UUID) -> uuid.UUID | None:
        return await session.scalar(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )

    async def _effective_scopes(
        user_id: uuid.UUID, organization_id: uuid.UUID, workspace_id: uuid.UUID | None
    ) -> frozenset[str]:
        return await query_effective_scopes(
            session, user_id, organization_id, workspace_id
        )

    monkeypatch.setattr(credentials, "_get_workspace_org_id", _workspace_org_id)
    monkeypatch.setattr(
        credentials, "_compute_effective_scopes_cached", _effective_scopes
    )


async def _workspace_viewer(session: AsyncSession) -> tuple[User, Workspace, Workspace]:
    """Create a user whose only role is viewer on one of two workspaces."""
    org_id = uuid.uuid4()
    org = Organization(
        id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:8]}", is_active=True
    )
    user = User(
        id=uuid.uuid4(),
        email=f"viewer-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
        role=UserRole.BASIC,
    )
    workspace = Workspace(id=uuid.uuid4(), name="Member of", organization_id=org.id)
    other = Workspace(id=uuid.uuid4(), name="Not member of", organization_id=org.id)
    session.add_all([org, user, workspace, other])
    await seed_system_scopes(session)
    await session.commit()
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
        slug="workspace-viewer",
    )
    await session.commit()
    return user, workspace, other


async def _resolve_role(
    session: AsyncSession, user: User, workspace_id: uuid.UUID | None
) -> Role:
    """Resolve the role as the optional-workspace route dependency does."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    return await _role_dependency(
        request=request,
        session=session,
        workspace_id=workspace_id,
        user=user,
        allow_user=True,
        allow_service=False,
        allow_api_key=False,
        require_workspace="optional",
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_workspace_role_reaches_route_with_workspace_id(
    session: AsyncSession, endpoint: Endpoint
) -> None:
    user, workspace, _ = await _workspace_viewer(session)
    token = ctx_role.set(None)
    try:
        role = await _resolve_role(session, user, workspace.id)
        assert role.workspace_id == workspace.id

        await endpoint(role, session)
    finally:
        ctx_role.reset(token)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_workspace_role_is_denied_without_workspace_id(
    session: AsyncSession, endpoint: Endpoint
) -> None:
    user, _, _ = await _workspace_viewer(session)
    token = ctx_role.set(None)
    try:
        role = await _resolve_role(session, user, None)
        assert "agent:read" not in (role.scopes or frozenset())

        with pytest.raises(ScopeDeniedError):
            await endpoint(role, session)
    finally:
        ctx_role.reset(token)


async def test_workspace_id_of_another_workspace_is_rejected(
    session: AsyncSession,
) -> None:
    user, _, other = await _workspace_viewer(session)
    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_role(session, user, other.id)
    finally:
        ctx_role.reset(token)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
