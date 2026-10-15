"""Agent access comes from workspace roles, and org agent routes are admin-only.

The member floor carries no agent scopes. Workspace pages read the model
config they need through workspace routes, and a workspace-only admin manages
the workspace's model subset without any org-level scope.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import grant_org_membership, grant_workspace_membership
from tracecat.agent import router as agent_router
from tracecat.agent.access import router as agent_access_router
from tracecat.agent.access.schemas import WorkspaceAgentModelAccessUpdate
from tracecat.agent.catalog import router as agent_catalog_router
from tracecat.agent.provider import router as agent_provider_router
from tracecat.agent.session import router as agent_session_router
from tracecat.auth import credentials
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.seeding import seed_system_roles_for_org, seed_system_scopes
from tracecat.authz.service import query_effective_scopes
from tracecat.contexts import ctx_role
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    AgentModelAccess,
    Organization,
    OrganizationSetting,
    User,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("db", "auth_reads_test_session"),
]

type OrgEndpoint = Callable[[Role, AsyncSession], Awaitable[object]]


async def _list_catalog(role: Role, session: AsyncSession) -> object:
    return await agent_catalog_router.list_catalog(
        role=role,
        session=session,
        provider=None,
        model_name=None,
        cursor=None,
        limit=50,
    )


async def _list_enabled_models(role: Role, session: AsyncSession) -> object:
    return await agent_access_router.list_enabled_models(
        role=role, session=session, workspace_id=None, cursor=None, limit=50
    )


async def _list_custom_providers(role: Role, session: AsyncSession) -> object:
    return await agent_provider_router.list_custom_providers(
        role=role, session=session, cursor=None, limit=50
    )


async def _get_default_model(role: Role, session: AsyncSession) -> object:
    return await agent_router.get_default_model(role=role, session=session)


async def _get_default_model_selection(role: Role, session: AsyncSession) -> object:
    return await agent_router.get_default_model_selection(role=role, session=session)


async def _get_providers_status(role: Role, session: AsyncSession) -> object:
    return await agent_router.get_providers_status(role=role, session=session)


ORG_READ_ENDPOINTS: list[OrgEndpoint] = [
    _list_catalog,
    _list_enabled_models,
    _list_custom_providers,
    _get_default_model,
    _get_default_model_selection,
    _get_providers_status,
]


@pytest.fixture
def auth_reads_test_session(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the auth lookups that open their own session at the test session.

    The test transaction is invisible to other connections, so the cached
    lookups would otherwise see neither the workspace nor the assignments.
    """
    test_session = session

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
    async def _auth_session() -> AsyncGenerator[AsyncSession]:
        yield test_session

    monkeypatch.setattr(credentials, "_get_workspace_org_id", _workspace_org_id)
    monkeypatch.setattr(
        credentials, "get_async_session_auth_context_manager", _auth_session
    )
    monkeypatch.setattr(
        credentials, "_compute_effective_scopes_cached", _effective_scopes
    )


@pytest.fixture(autouse=True)
def reset_ctx_role() -> Iterator[None]:
    """Isolate the role context per test."""
    token = ctx_role.set(None)
    try:
        yield
    finally:
        ctx_role.reset(token)


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


async def _workspace(session: AsyncSession, org: Organization) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(), name=f"ws-{uuid.uuid4().hex[:8]}", organization_id=org.id
    )
    session.add(workspace)
    await session.commit()
    return workspace


async def _workspace_user(
    session: AsyncSession, org: Organization, workspace: Workspace, slug: str
) -> User:
    """Create a user whose only role is ``slug`` on ``workspace``."""
    user = await _user(session)
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
        slug=slug,
    )
    await session.commit()
    return user


async def _resolve_role(
    session: AsyncSession, user: User, workspace_id: uuid.UUID | None
) -> Role:
    """Resolve a user's role as the route dependency does."""
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
        require_workspace="yes" if workspace_id else "no",
    )


async def _enable_org_models(
    session: AsyncSession, org: Organization, count: int
) -> list[AgentCatalog]:
    """Create ``count`` org catalog rows, each enabled at the org level."""
    rows = [
        AgentCatalog(
            organization_id=org.id,
            custom_provider_id=None,
            model_provider="openai",
            model_name=f"model-{index}-{uuid.uuid4().hex[:6]}",
            model_metadata={},
        )
        for index in range(count)
    ]
    session.add_all(rows)
    await session.flush()
    session.add_all(
        AgentModelAccess(organization_id=org.id, catalog_id=row.id) for row in rows
    )
    await session.commit()
    return rows


async def _set_org_default(
    session: AsyncSession, org: Organization, catalog: AgentCatalog
) -> None:
    session.add(
        OrganizationSetting(
            organization_id=org.id,
            key="agent_default_model_catalog_id",
            value=f'"{catalog.id}"'.encode(),
            value_type="json",
            is_encrypted=False,
        )
    )
    await session.commit()


async def _put_access(
    role: Role,
    session: AsyncSession,
    workspace: Workspace,
    params: WorkspaceAgentModelAccessUpdate,
):
    return await agent_catalog_router.update_workspace_model_access(
        workspace_id=workspace.id, role=role, session=session, params=params
    )


async def test_intake_only_user_has_no_agent_scopes(session: AsyncSession) -> None:
    """An Intake only user, with or without the legacy member role, holds no agent scope."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    user = await _workspace_user(session, org, workspace, "workspace-intake")
    # Legacy assignments of organization-member still read its role links.
    await grant_org_membership(
        session, user_id=user.id, organization_id=org.id, slug="organization-member"
    )
    await session.commit()

    for workspace_id in (workspace.id, None):
        scopes = await query_effective_scopes(session, user.id, org.id, workspace_id)
        assert {scope for scope in scopes if scope.startswith("agent:")} == set()
    assert "form:submit" in await query_effective_scopes(
        session, user.id, org.id, workspace.id
    )


async def test_viewer_reads_agents_but_cannot_run_them(session: AsyncSession) -> None:
    """A workspace viewer lists models and sessions but cannot start a session."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    viewer = await _workspace_user(session, org, workspace, "workspace-viewer")
    role = await _resolve_role(session, viewer, workspace.id)

    assert "agent:read" in (role.scopes or frozenset())
    assert "agent:execute" not in (role.scopes or frozenset())
    await agent_catalog_router.get_workspace_models(
        workspace_id=workspace.id, role=role, session=session
    )
    await agent_session_router.list_agent_backends(role=role)
    with pytest.raises(ScopeDeniedError):
        await agent_session_router.create_session(
            request=MagicMock(), role=role, session=session
        )


async def test_editor_can_run_agents(session: AsyncSession) -> None:
    """A workspace editor holds agent:execute from the workspace role alone."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    editor = await _workspace_user(session, org, workspace, "workspace-editor")
    role = await _resolve_role(session, editor, workspace.id)

    assert {"agent:read", "agent:execute"} <= (role.scopes or frozenset())


async def test_workspace_only_admin_manages_workspace_model_access(
    session: AsyncSession,
) -> None:
    """A workspace-only admin narrows, reads and clears the workspace's model subset."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    admin = await _workspace_user(session, org, workspace, "workspace-admin")
    first, second = await _enable_org_models(session, org, 2)
    role = await _resolve_role(session, admin, workspace.id)

    initial = await agent_catalog_router.get_workspace_model_access(
        workspace_id=workspace.id, role=role, session=session
    )
    assert initial.inherit_all is True
    assert initial.catalog_ids == []
    assert {candidate.id for candidate in initial.candidates} == {first.id, second.id}

    narrowed = await _put_access(
        role,
        session,
        workspace,
        WorkspaceAgentModelAccessUpdate(inherit_all=False, catalog_ids=[first.id]),
    )
    assert narrowed.inherit_all is False
    assert narrowed.catalog_ids == [first.id]
    models = await agent_catalog_router.get_workspace_models(
        workspace_id=workspace.id, role=role, session=session
    )
    assert [model.id for model in models.items] == [first.id]

    cleared = await _put_access(
        role, session, workspace, WorkspaceAgentModelAccessUpdate(inherit_all=True)
    )
    assert cleared.inherit_all is True
    assert cleared.catalog_ids == []
    remaining = await session.scalars(
        select(AgentModelAccess).where(AgentModelAccess.workspace_id == workspace.id)
    )
    assert remaining.all() == []


async def test_workspace_model_access_rejects_models_the_org_has_not_enabled(
    session: AsyncSession,
) -> None:
    """A workspace subset can only pick from org-enabled models."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    admin = await _workspace_user(session, org, workspace, "workspace-admin")
    (enabled,) = await _enable_org_models(session, org, 1)
    not_enabled = AgentCatalog(
        organization_id=org.id,
        custom_provider_id=None,
        model_provider="openai",
        model_name=f"model-off-{uuid.uuid4().hex[:6]}",
        model_metadata={},
    )
    session.add(not_enabled)
    await session.commit()
    role = await _resolve_role(session, admin, workspace.id)

    with pytest.raises(HTTPException) as exc_info:
        await _put_access(
            role,
            session,
            workspace,
            WorkspaceAgentModelAccessUpdate(
                inherit_all=False, catalog_ids=[enabled.id, not_enabled.id]
            ),
        )
    assert exc_info.value.status_code == 400
    remaining = await session.scalars(
        select(AgentModelAccess).where(AgentModelAccess.workspace_id == workspace.id)
    )
    assert remaining.all() == []


async def test_viewer_cannot_change_workspace_model_access(
    session: AsyncSession,
) -> None:
    """Writing the workspace subset needs agent:update, which viewers lack."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    viewer = await _workspace_user(session, org, workspace, "workspace-viewer")
    (model,) = await _enable_org_models(session, org, 1)
    role = await _resolve_role(session, viewer, workspace.id)

    with pytest.raises(ScopeDeniedError):
        await _put_access(
            role,
            session,
            workspace,
            WorkspaceAgentModelAccessUpdate(inherit_all=False, catalog_ids=[model.id]),
        )


async def test_workspace_default_is_the_org_default_only_when_enabled_here(
    session: AsyncSession,
) -> None:
    """The workspace default is the org default, and only while the workspace can use it."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    admin = await _workspace_user(session, org, workspace, "workspace-admin")
    default, other = await _enable_org_models(session, org, 2)
    await _set_org_default(session, org, default)
    role = await _resolve_role(session, admin, workspace.id)

    async def _models():
        return await agent_catalog_router.get_workspace_models(
            workspace_id=workspace.id, role=role, session=session
        )

    inherited = await _models()
    assert inherited.default_catalog_id == default.id
    assert inherited.has_org_default is True

    await _put_access(
        role,
        session,
        workspace,
        WorkspaceAgentModelAccessUpdate(inherit_all=False, catalog_ids=[other.id]),
    )
    excluded = await _models()
    assert excluded.default_catalog_id is None
    assert excluded.has_org_default is True


async def test_workspace_models_carry_custom_provider_labels(
    session: AsyncSession,
) -> None:
    """Workspace pages get custom provider names from the workspace route."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    viewer = await _workspace_user(session, org, workspace, "workspace-viewer")
    provider = AgentCustomProvider(
        organization_id=org.id,
        display_name="Synthetic gateway",
        base_url="https://gateway.example.com/v1",
    )
    session.add(provider)
    await session.flush()
    catalog = AgentCatalog(
        organization_id=org.id,
        custom_provider_id=provider.id,
        model_provider="custom-model-provider",
        model_name="synthetic-model",
        model_metadata={},
    )
    session.add(catalog)
    await session.flush()
    session.add(AgentModelAccess(organization_id=org.id, catalog_id=catalog.id))
    await session.commit()
    role = await _resolve_role(session, viewer, workspace.id)

    models = await agent_catalog_router.get_workspace_models(
        workspace_id=workspace.id, role=role, session=session
    )

    (item,) = models.items
    assert item.custom_provider_name == "Synthetic gateway"
    assert item.custom_provider_base_url == "https://gateway.example.com/v1"
    assert models.has_org_default is False


@pytest.mark.parametrize("endpoint", ORG_READ_ENDPOINTS)
async def test_org_agent_routes_are_admin_only(
    session: AsyncSession, endpoint: OrgEndpoint
) -> None:
    """Members and workspace-only admins are denied org agent routes; org admins pass."""
    org = await _org(session)
    workspace = await _workspace(session, org)
    member = await _user(session)
    await grant_org_membership(session, user_id=member.id, organization_id=org.id)
    workspace_admin = await _workspace_user(session, org, workspace, "workspace-admin")
    org_admin = await _user(session)
    await grant_org_membership(
        session,
        user_id=org_admin.id,
        organization_id=org.id,
        slug="organization-admin",
    )
    await session.commit()

    for denied in (member, workspace_admin):
        role = await _resolve_role(session, denied, None)
        with pytest.raises(ScopeDeniedError):
            await endpoint(role, session)

    await endpoint(await _resolve_role(session, org_admin, None), session)
