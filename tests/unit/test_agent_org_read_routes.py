"""Org-level agent reads that workspace pages call.

The member floor carries no agent scopes, so a user whose roles are assigned
per workspace reaches these routes only when the request names the workspace.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
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
from tracecat.auth.api_keys import generate_managed_api_key
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
    Scope,
    ServiceAccount,
    ServiceAccountApiKey,
    User,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import ScopeDeniedError
from tracecat.tiers import defaults as tier_defaults

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
    role: Role, session: AsyncSession, *, include_org_rows: bool = False
) -> AgentModelAccessListResponse:
    return await agent_access_router.list_enabled_models(
        role=role,
        session=session,
        include_org_rows=include_org_rows,
        cursor=None,
        limit=50,
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
    test_session = session
    """Point the auth lookups that open their own session at the test session.

    The test transaction is invisible to other connections, so the cached
    lookups would otherwise see neither the workspace nor the assignments.
    """

    async def _workspace_org_id(
        workspace_id: uuid.UUID, *, session: object | None = None
    ) -> uuid.UUID | None:
        return await test_session.scalar(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )

    async def _effective_scopes(
        user_id: uuid.UUID, organization_id: uuid.UUID, workspace_id: uuid.UUID | None
    ) -> frozenset[str]:
        return await query_effective_scopes(
            test_session, user_id, organization_id, workspace_id
        )

    @asynccontextmanager
    async def _auth_session() -> AsyncIterator[AsyncSession]:
        yield test_session

    monkeypatch.setattr(credentials, "_get_workspace_org_id", _workspace_org_id)
    monkeypatch.setattr(
        credentials, "get_async_session_auth_context_manager", _auth_session
    )
    # API-key authentication requires the service accounts entitlement.
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
            update={"service_accounts": True}
        ),
    )
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
) -> Role:
    """Resolve a user's role as the optional-workspace dependency does."""
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
        allow_api_key=False,
        require_workspace="optional",
    )


async def _resolve_api_key_role(
    session: AsyncSession, api_key: str, workspace_id: uuid.UUID | None
) -> Role:
    """Resolve an API key's role as the catalog list dependency does."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.cookies = {}
    return await _role_dependency(
        request=request,
        session=session,
        workspace_id=workspace_id,
        user=None,
        tracecat_api_key=api_key,
        allow_user=True,
        allow_service=False,
        allow_api_key=True,
        require_workspace="optional",
    )


async def _service_account_key(
    session: AsyncSession,
    org: Organization,
    *,
    bound_workspace: Workspace | None = None,
) -> str:
    """Create a service account holding `agent:read` and return its raw key."""
    scopes = await session.scalars(
        select(Scope).where(Scope.organization_id.is_(None), Scope.name == "agent:read")
    )
    generated = generate_managed_api_key(
        prefix="tc_ws_sk_" if bound_workspace else "tc_org_sk_"
    )
    service_account = ServiceAccount(
        organization_id=org.id,
        workspace_id=bound_workspace.id if bound_workspace else None,
        name=f"automation-{uuid.uuid4().hex[:8]}",
        scopes=list(scopes.all()),
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
    return generated.raw


async def _model_access_rows(
    session: AsyncSession, org: Organization, workspace: Workspace, other: Workspace
) -> None:
    """Enable one model at the org level and on each of two workspaces."""
    catalog = AgentCatalog(
        organization_id=org.id,
        custom_provider_id=None,
        model_provider="openai",
        model_name=f"model-{uuid.uuid4().hex[:8]}",
        model_metadata={},
    )
    session.add(catalog)
    await session.flush()
    session.add_all(
        [
            AgentModelAccess(organization_id=org.id, catalog_id=catalog.id),
            AgentModelAccess(
                organization_id=org.id,
                catalog_id=catalog.id,
                workspace_id=workspace.id,
            ),
            AgentModelAccess(
                organization_id=org.id, catalog_id=catalog.id, workspace_id=other.id
            ),
        ]
    )
    await session.commit()


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


async def test_workspace_id_of_another_organization_is_rejected(
    session: AsyncSession,
) -> None:
    """Every converted route resolves its role here, so none is reached."""
    user, _, _ = await _workspace_viewer(session)
    foreign_org = await _org(session)
    foreign_workspace = await _workspace(session, foreign_org, "Foreign")

    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_role(session, user, foreign_workspace.id)
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


@pytest.mark.parametrize("with_workspace", [False, True])
async def test_org_api_key_lists_catalog_with_and_without_workspace_id(
    session: AsyncSession, with_workspace: bool
) -> None:
    org = await _org(session)
    workspace = await _workspace(session, org, "Own workspace")
    api_key = await _service_account_key(session, org)

    token = ctx_role.set(None)
    try:
        role = await _resolve_api_key_role(
            session, api_key, workspace.id if with_workspace else None
        )
        assert role.type == "service_account"
        assert role.organization_id == org.id
        assert role.workspace_id == (workspace.id if with_workspace else None)
        assert role.bound_workspace_id is None

        await _list_catalog(role, session)
    finally:
        ctx_role.reset(token)


async def test_org_api_key_with_unknown_workspace_id_is_rejected(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    api_key = await _service_account_key(session, org)

    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_api_key_role(session, api_key, uuid.uuid4())
        assert ctx_role.get() is None
    finally:
        ctx_role.reset(token)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


async def test_org_api_key_with_another_organizations_workspace_is_rejected(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    api_key = await _service_account_key(session, org)
    foreign_org = await _org(session)
    foreign_workspace = await _workspace(session, foreign_org, "Foreign")

    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_api_key_role(session, api_key, foreign_workspace.id)
        assert ctx_role.get() is None
    finally:
        ctx_role.reset(token)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


async def test_workspace_bound_api_key_cannot_name_another_workspace(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    bound = await _workspace(session, org, "Bound")
    other = await _workspace(session, org, "Other")
    api_key = await _service_account_key(session, org, bound_workspace=bound)

    token = ctx_role.set(None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_api_key_role(session, api_key, other.id)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert ctx_role.get() is None

        # Bound keys keep being refused the org catalog, as before.
        role = await _resolve_api_key_role(session, api_key, None)
        assert role.bound_workspace_id == bound.id
        with pytest.raises(HTTPException) as catalog_exc:
            await _list_catalog(role, session)
        assert catalog_exc.value.status_code == status.HTTP_403_FORBIDDEN
    finally:
        ctx_role.reset(token)


async def test_model_access_list_row_sets_for_an_org_admin(
    session: AsyncSession,
) -> None:
    org = await _org(session)
    admin = await _user(session)
    workspace = await _workspace(session, org, "Named")
    other = await _workspace(session, org, "Other")
    await grant_org_membership(
        session, user_id=admin.id, organization_id=org.id, slug="organization-admin"
    )
    await _model_access_rows(session, org, workspace, other)

    token = ctx_role.set(None)
    try:
        org_role = await _resolve_role(session, admin, None)
        everything = await _list_enabled_models(org_role, session)
        # The flag only widens a workspace-filtered list.
        everything_flagged = await _list_enabled_models(
            org_role, session, include_org_rows=True
        )

        workspace_role = await _resolve_role(session, admin, workspace.id)
        workspace_only = await _list_enabled_models(workspace_role, session)
        with_org_rows = await _list_enabled_models(
            workspace_role, session, include_org_rows=True
        )
    finally:
        ctx_role.reset(token)

    all_rows = {None, workspace.id, other.id}
    assert {item.workspace_id for item in everything.items} == all_rows
    assert {item.workspace_id for item in everything_flagged.items} == all_rows
    # `workspace_id` alone keeps its original meaning: that workspace's rows.
    assert {item.workspace_id for item in workspace_only.items} == {workspace.id}
    assert {item.workspace_id for item in with_org_rows.items} == {
        None,
        workspace.id,
    }


async def test_model_access_list_row_sets_for_a_workspace_only_role(
    session: AsyncSession,
) -> None:
    user, workspace, other = await _workspace_viewer(session)
    org = await session.get(Organization, workspace.organization_id)
    assert org is not None
    await _model_access_rows(session, org, workspace, other)

    token = ctx_role.set(None)
    try:
        role = await _resolve_role(session, user, workspace.id)
        workspace_only = await _list_enabled_models(role, session)
        with_org_rows = await _list_enabled_models(role, session, include_org_rows=True)

        # Another workspace's rows are out of reach on every path.
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_role(session, user, other.id)
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

        org_role = await _resolve_role(session, user, None)
        with pytest.raises(ScopeDeniedError):
            await _list_enabled_models(org_role, session, include_org_rows=True)
    finally:
        ctx_role.reset(token)

    assert {item.workspace_id for item in workspace_only.items} == {workspace.id}
    assert {item.workspace_id for item in with_org_rows.items} == {
        None,
        workspace.id,
    }


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
