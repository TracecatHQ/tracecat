"""Org-level agent reads that workspace pages call.

The member floor carries no agent scopes, so a user whose roles are assigned
per workspace reaches these routes only when the request names the workspace.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import get_type_hints
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import grant_org_membership, grant_workspace_membership
from tracecat.agent import router as agent_router
from tracecat.agent.access import router as agent_access_router
from tracecat.agent.access.schemas import AgentModelAccessListResponse
from tracecat.agent.catalog import router as agent_catalog_router
from tracecat.agent.provider import router as agent_provider_router
from tracecat.agent.session import router as agent_session_router
from tracecat.auth import credentials
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.dependencies import (
    OrgActorOptionalWorkspaceRole,
    OrgUserOptionalWorkspaceRole,
)
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.membership import ensure_member
from tracecat.authz.seeding import seed_system_roles_for_org, seed_system_scopes
from tracecat.authz.service import query_effective_scopes
from tracecat.contexts import ctx_role
from tracecat.db.models import (
    AgentCatalog,
    AgentModelAccess,
    Group,
    GroupMember,
    GroupRoleAssignment,
    Organization,
    User,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import ScopeDeniedError

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("db", "auth_reads_test_session"),
]

type Endpoint = Callable[[Role, AsyncSession], Awaitable[object]]
type BareEndpoint = Callable[..., Awaitable[object]]


async def _get_default_model(role: Role, session: AsyncSession) -> object:
    return await agent_router.get_default_model(role=role, session=session)


async def _get_default_model_selection(role: Role, session: AsyncSession) -> object:
    return await agent_router.get_default_model_selection(role=role, session=session)


async def _list_custom_providers(role: Role, session: AsyncSession) -> object:
    return await agent_provider_router.list_custom_providers(
        role=role, session=session, cursor=None, limit=50
    )


async def _list_catalog(role: Role, session: AsyncSession) -> object:
    return await agent_catalog_router.list_catalog(
        role=role,
        session=session,
        provider=None,
        model_name=None,
        cursor=None,
        limit=50,
    )


async def _list_enabled_models(
    role: Role, session: AsyncSession
) -> AgentModelAccessListResponse:
    return await agent_access_router.list_enabled_models(
        role=role, session=session, cursor=None, limit=50
    )


ENDPOINTS: list[Endpoint] = [
    _get_default_model,
    _get_default_model_selection,
    _list_custom_providers,
    _list_catalog,
    _list_enabled_models,
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


async def _org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(
        id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:8]}", is_active=True
    )
    session.add(org)
    await seed_system_scopes(session)
    await session.flush()
    await seed_system_roles_for_org(session, org.id)
    await session.commit()
    return org


async def _user(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
        role=UserRole.BASIC,
    )
    session.add(user)
    await session.commit()
    return user


async def _workspace(session: AsyncSession, org: Organization, name: str) -> Workspace:
    workspace = Workspace(id=uuid.uuid4(), name=name, organization_id=org.id)
    session.add(workspace)
    await session.commit()
    return workspace


async def _workspace_viewer(session: AsyncSession) -> tuple[User, Workspace, Workspace]:
    """Create a user whose only role is viewer on one of two workspaces."""
    org = await _org(session)
    user = await _user(session)
    workspace = await _workspace(session, org, "Member of")
    other = await _workspace(session, org, "Not member of")
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
    session: AsyncSession,
    user: User,
    workspace_id: uuid.UUID | None,
    *,
    allow_api_key: bool = False,
) -> Role:
    """Resolve the role as the optional-workspace route dependency does."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.cookies = {}
    return await _role_dependency(
        request=request,
        session=session,
        workspace_id=workspace_id,
        user=user,
        allow_user=True,
        allow_service=False,
        allow_api_key=allow_api_key,
        require_workspace="optional",
    )


def test_converted_routes_use_the_optional_workspace_dependency() -> None:
    user_only = [
        agent_router.get_default_model,
        agent_router.get_default_model_selection,
        agent_provider_router.list_custom_providers,
        agent_access_router.list_enabled_models,
    ]
    for endpoint in user_only:
        hints = get_type_hints(endpoint, include_extras=True)
        assert hints["role"] == OrgUserOptionalWorkspaceRole
        # The dependency owns `workspace_id`; the route must not redeclare it.
        assert "workspace_id" not in hints

    catalog_hints = get_type_hints(
        agent_catalog_router.list_catalog, include_extras=True
    )
    assert catalog_hints["role"] == OrgActorOptionalWorkspaceRole
    assert "workspace_id" not in catalog_hints


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


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_group_assigned_workspace_role_reaches_route(
    session: AsyncSession, endpoint: Endpoint
) -> None:
    org = await _org(session)
    user = await _user(session)
    workspace = await _workspace(session, org, "Group workspace")
    viewer_role_id = await session.scalar(
        select(DBRole.id).where(
            DBRole.organization_id == org.id, DBRole.slug == "workspace-viewer"
        )
    )
    assert viewer_role_id is not None
    group = Group(id=uuid.uuid4(), name="Viewers", organization_id=org.id)
    session.add(group)
    await session.flush()
    await ensure_member(session, org.id, user.id)
    session.add_all(
        [
            GroupMember(group_id=group.id, user_id=user.id, organization_id=org.id),
            GroupRoleAssignment(
                organization_id=org.id,
                group_id=group.id,
                workspace_id=workspace.id,
                role_id=viewer_role_id,
            ),
        ]
    )
    await session.commit()

    token = ctx_role.set(None)
    try:
        role = await _resolve_role(session, user, workspace.id)
        await endpoint(role, session)

        org_role = await _resolve_role(session, user, None)
        with pytest.raises(ScopeDeniedError):
            await endpoint(org_role, session)
    finally:
        ctx_role.reset(token)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("with_workspace", [True, False])
async def test_org_admin_reaches_route_with_and_without_workspace_id(
    session: AsyncSession, endpoint: Endpoint, with_workspace: bool
) -> None:
    org = await _org(session)
    admin = await _user(session)
    workspace = await _workspace(session, org, "Any workspace")
    await grant_org_membership(
        session, user_id=admin.id, organization_id=org.id, slug="organization-admin"
    )
    await session.commit()

    token = ctx_role.set(None)
    try:
        role = await _resolve_role(
            session, admin, workspace.id if with_workspace else None
        )
        assert role.workspace_id == (workspace.id if with_workspace else None)

        await endpoint(role, session)
    finally:
        ctx_role.reset(token)


@pytest.mark.parametrize("allow_api_key", [False, True])
async def test_workspace_id_of_another_workspace_is_rejected(
    session: AsyncSession, allow_api_key: bool
) -> None:
    user, _, other = await _workspace_viewer(session)
    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_role(session, user, other.id, allow_api_key=allow_api_key)
    finally:
        ctx_role.reset(token)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize("allow_api_key", [False, True])
async def test_workspace_id_of_another_organization_is_rejected(
    session: AsyncSession, allow_api_key: bool
) -> None:
    """Every converted route resolves its role here, so none is reached."""
    user, _, _ = await _workspace_viewer(session)
    foreign_org = await _org(session)
    foreign_workspace = await _workspace(session, foreign_org, "Foreign")

    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_role(
                session, user, foreign_workspace.id, allow_api_key=allow_api_key
            )
        assert ctx_role.get() is None
    finally:
        ctx_role.reset(token)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


async def test_unknown_workspace_id_is_rejected(session: AsyncSession) -> None:
    user, _, _ = await _workspace_viewer(session)
    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_role(session, user, uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


async def test_model_access_list_is_scoped_to_the_named_workspace(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    admin = await _user(session)
    workspace = await _workspace(session, org, "Named")
    other = await _workspace(session, org, "Other")
    await grant_org_membership(
        session, user_id=admin.id, organization_id=org.id, slug="organization-admin"
    )
    catalog = AgentCatalog(
        organization_id=org.id,
        custom_provider_id=None,
        model_provider="openai",
        model_name=f"model-{uuid.uuid4().hex[:8]}",
        model_metadata={},
    )
    session.add(catalog)
    await session.flush()
    rows = {
        None: AgentModelAccess(organization_id=org.id, catalog_id=catalog.id),
        workspace.id: AgentModelAccess(
            organization_id=org.id, catalog_id=catalog.id, workspace_id=workspace.id
        ),
        other.id: AgentModelAccess(
            organization_id=org.id, catalog_id=catalog.id, workspace_id=other.id
        ),
    }
    session.add_all(rows.values())
    await session.commit()

    token = ctx_role.set(None)
    try:
        org_role = await _resolve_role(session, admin, None)
        everything = await _list_enabled_models(org_role, session)

        workspace_role = await _resolve_role(session, admin, workspace.id)
        scoped = await _list_enabled_models(workspace_role, session)
    finally:
        ctx_role.reset(token)

    # Without a workspace the list is unchanged: every row in the organization.
    assert {item.workspace_id for item in everything.items} == {
        None,
        workspace.id,
        other.id,
    }
    assert {item.workspace_id for item in scoped.items} == {None, workspace.id}


async def test_workspace_viewer_cannot_run_agents(session: AsyncSession) -> None:
    """The API maps `ScopeDeniedError` to a 403 response."""
    user, workspace, _ = await _workspace_viewer(session)
    token = ctx_role.set(None)
    try:
        role = await _resolve_role(session, user, workspace.id)
        assert "agent:read" in (role.scopes or frozenset())
        assert "agent:execute" not in (role.scopes or frozenset())

        # The scope check runs before the route binds its arguments.
        endpoints: list[BareEndpoint] = [
            agent_session_router.create_session,
            agent_session_router.send_message,
        ]
        for endpoint in endpoints:
            with pytest.raises(ScopeDeniedError):
                await endpoint()
    finally:
        ctx_role.reset(token)


async def test_workspace_editor_passes_the_agent_execute_check(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    user = await _user(session)
    workspace = await _workspace(session, org, "Editor workspace")
    await grant_workspace_membership(
        session, user_id=user.id, organization_id=org.id, workspace_id=workspace.id
    )
    await session.commit()

    token = ctx_role.set(None)
    try:
        role = await _resolve_role(session, user, workspace.id)
        assert "agent:execute" in (role.scopes or frozenset())

        # Past the scope check, the bare call fails on its missing arguments.
        endpoints: list[BareEndpoint] = [agent_session_router.create_session]
        for endpoint in endpoints:
            with pytest.raises(TypeError):
                await endpoint()
    finally:
        ctx_role.reset(token)
