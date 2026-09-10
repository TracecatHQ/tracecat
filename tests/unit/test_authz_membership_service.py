"""Unit tests for MembershipService."""

import contextlib
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import (
    grant_org_membership,
    grant_org_membership_via_group,
    grant_workspace_membership,
)
from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz import service as authz_service
from tracecat.authz.scopes import ADMIN_SCOPES, EDITOR_SCOPES, VIEWER_SCOPES
from tracecat.authz.seeding import seed_system_scopes
from tracecat.authz.service import MembershipService
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    Membership,
    Organization,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.workspaces.schemas import WorkspaceMembershipCreate

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture(autouse=True)
def bypass_session_is_test_session(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route the RLS-bypass session to the test session.

    The real helper opens its own connection, which cannot see the rows this
    test's uncommitted transaction holds. RLS is not enforced here anyway:
    the ``db`` fixture builds tables with ``create_all``, and the policies come
    from migrations.
    """

    @contextlib.asynccontextmanager
    async def _bypass() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        authz_service, "get_async_session_bypass_rls_context_manager", _bypass
    )


@pytest.fixture
async def organization(session: AsyncSession) -> Organization:
    """Create a test organization."""
    org = Organization(
        id=uuid.uuid4(),
        name="Test Org",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def workspace(session: AsyncSession, organization: Organization) -> Workspace:
    """Create a test workspace."""
    ws = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=organization.id,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


@pytest.fixture
async def actor_user(session: AsyncSession) -> User:
    """Create the acting user for membership operations."""
    user = User(
        id=uuid.uuid4(),
        email=f"actor-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.ADMIN,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def member_user(session: AsyncSession) -> User:
    """Create the target workspace member user."""
    user = User(
        id=uuid.uuid4(),
        email=f"member-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def workspace_editor_role(
    session: AsyncSession, organization: Organization
) -> DBRole:
    """Create the default workspace-editor role required by create_membership."""
    role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Editor",
        slug="workspace-editor",
        description="Default editor role",
        organization_id=organization.id,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


@pytest.fixture
def actor_role(
    organization: Organization, workspace: Workspace, actor_user: User
) -> Role:
    """Create a role with scopes required for membership management."""
    return Role(
        type="user",
        user_id=actor_user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )


@pytest.fixture
def membership_service(session: AsyncSession, actor_role: Role) -> MembershipService:
    """Create MembershipService under a role with admin workspace scopes."""
    return MembershipService(session=session, role=actor_role)


async def test_delete_membership_removes_membership_and_assignment(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Deleting membership should also delete workspace direct role assignment."""
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
            assigned_by=actor_user.id,
        )
    )
    session.add(LegacyMembership(user_id=member_user.id, workspace_id=workspace.id))
    await session.commit()

    await membership_service.delete_membership(
        workspace_id=workspace.id,
        user_id=member_user.id,
    )

    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == member_user.id,
        )
    )
    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.workspace_id == workspace.id,
            UserRoleAssignment.user_id == member_user.id,
        )
    )

    legacy = await session.scalar(
        select(LegacyMembership).where(
            LegacyMembership.workspace_id == workspace.id,
            LegacyMembership.user_id == member_user.id,
        )
    )

    assert membership is None
    assert assignment is None
    # The legacy table is kept in step for older app versions.
    assert legacy is None


async def test_delete_membership_removes_orphan_assignment(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Delete should clean orphan assignments even when membership row is missing."""
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
            assigned_by=actor_user.id,
        )
    )
    await session.commit()

    await membership_service.delete_membership(
        workspace_id=workspace.id,
        user_id=member_user.id,
    )

    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.workspace_id == workspace.id,
            UserRoleAssignment.user_id == member_user.id,
        )
    )

    assert assignment is None


async def test_create_membership_duplicate_raises_conflict(
    session: AsyncSession,
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Creating an existing membership should raise an integrity conflict."""
    assert workspace_editor_role.slug == "workspace-editor"
    await grant_workspace_membership(
        session,
        user_id=member_user.id,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
    )
    await session.commit()

    with pytest.raises(TracecatConflictError):
        await membership_service.create_membership(
            workspace_id=workspace.id,
            params=WorkspaceMembershipCreate(user_id=member_user.id),
        )


@pytest.fixture
async def scoped_workspace_editor_role(
    session: AsyncSession,
    organization: Organization,
    workspace_editor_role: DBRole,
) -> DBRole:
    """Attach real editor scopes to the default role.

    Without backing RoleScope rows the grant ceiling is vacuous, so escalation
    through membership creation would go unnoticed.
    """
    await seed_system_scopes(session)
    result = await session.execute(
        select(Scope).where(Scope.name.in_(sorted(EDITOR_SCOPES)))
    )
    for scope in result.scalars().all():
        session.add(RoleScope(role_id=workspace_editor_role.id, scope_id=scope.id))
    await session.commit()
    return workspace_editor_role


async def test_create_membership_rejects_inviter_without_editor_scopes(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    actor_user: User,
    member_user: User,
    scoped_workspace_editor_role: DBRole,
) -> None:
    """A caller holding only the invite scope cannot grant the editor role.

    Membership creation assigns workspace-editor, so an unbounded write would
    let a single scope escalate into the full editor scope set.
    """
    inviter_role = DBRole(
        id=uuid.uuid4(),
        name="Inviter Only",
        slug=None,
        organization_id=organization.id,
    )
    session.add(inviter_role)
    await session.flush()
    invite_scope = (
        (
            await session.execute(
                select(Scope).where(Scope.name == "workspace:member:invite")
            )
        )
        .scalars()
        .one()
    )
    session.add(RoleScope(role_id=inviter_role.id, scope_id=invite_scope.id))
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=actor_user.id,
            workspace_id=workspace.id,
            role_id=inviter_role.id,
        )
    )
    await session.commit()

    service = MembershipService(
        session=session,
        role=Role(
            type="user",
            user_id=actor_user.id,
            organization_id=organization.id,
            workspace_id=workspace.id,
            service_id="tracecat-api",
            scopes=frozenset({"workspace:member:invite"}),
        ),
    )

    with pytest.raises(
        TracecatAuthorizationError,
        match="Cannot grant scopes not held by the caller",
    ):
        await service.create_membership(
            workspace_id=workspace.id,
            params=WorkspaceMembershipCreate(user_id=member_user.id),
        )


async def test_create_membership_allows_admin_inviter(
    session: AsyncSession,
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    scoped_workspace_editor_role: DBRole,
    organization: Organization,
    actor_user: User,
) -> None:
    """An admin inviter still grants membership once the ceiling applies."""
    admin_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Admin",
        slug=None,
        organization_id=organization.id,
    )
    session.add(admin_role)
    await session.flush()
    result = await session.execute(
        select(Scope).where(Scope.name.in_(sorted(ADMIN_SCOPES)))
    )
    for scope in result.scalars().all():
        session.add(RoleScope(role_id=admin_role.id, scope_id=scope.id))
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=actor_user.id,
            workspace_id=workspace.id,
            role_id=admin_role.id,
        )
    )
    await grant_org_membership(
        session, user_id=member_user.id, organization_id=organization.id
    )
    await session.commit()

    await membership_service.create_membership(
        workspace_id=workspace.id,
        params=WorkspaceMembershipCreate(user_id=member_user.id),
    )

    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == member_user.id,
                Membership.workspace_id == workspace.id,
            )
        )
    ).scalar_one_or_none()
    assert membership is not None

    # The legacy table is kept in step for older app versions.
    legacy = (
        await session.execute(
            select(LegacyMembership).where(
                LegacyMembership.user_id == member_user.id,
                LegacyMembership.workspace_id == workspace.id,
            )
        )
    ).scalar_one_or_none()
    assert legacy is not None


async def test_list_workspace_members_reports_each_path_once(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """One row per member; a direct assignment outranks a group grant."""
    group_role = DBRole(
        name="Reviewer",
        slug=None,
        description=None,
        organization_id=organization.id,
    )
    group = Group(name="Reviewers", organization_id=organization.id)
    session.add_all([group_role, group])
    await session.flush()
    # actor_user: group only. member_user: group and direct.
    session.add_all(
        [
            GroupMember(group_id=group.id, user_id=actor_user.id),
            GroupMember(group_id=group.id, user_id=member_user.id),
            GroupRoleAssignment(
                organization_id=organization.id,
                group_id=group.id,
                workspace_id=workspace.id,
                role_id=group_role.id,
            ),
        ]
    )
    await grant_workspace_membership(
        session,
        user_id=member_user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )
    await session.commit()

    members = await membership_service.list_workspace_members(workspace.id)

    by_user = {m.user_id: m.role_name for m in members}
    assert len(members) == len(by_user) == 2
    assert by_user[actor_user.id] == "Reviewer"
    assert by_user[member_user.id] == workspace_editor_role.name


async def test_delete_membership_rejects_when_group_grant_remains(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """A workspace-scoped group grant blocks the delete and mutates nothing."""
    group_role = DBRole(
        name="Reviewer",
        slug=None,
        description=None,
        organization_id=organization.id,
    )
    group = Group(name="Reviewers", organization_id=organization.id)
    session.add_all([group_role, group])
    await session.flush()
    session.add_all(
        [
            GroupMember(group_id=group.id, user_id=member_user.id),
            GroupRoleAssignment(
                organization_id=organization.id,
                group_id=group.id,
                workspace_id=workspace.id,
                role_id=group_role.id,
            ),
            UserRoleAssignment(
                organization_id=organization.id,
                user_id=member_user.id,
                workspace_id=workspace.id,
                role_id=workspace_editor_role.id,
                assigned_by=actor_user.id,
            ),
            LegacyMembership(user_id=member_user.id, workspace_id=workspace.id),
        ]
    )
    await session.commit()

    with pytest.raises(TracecatConflictError, match="Reviewers"):
        await membership_service.delete_membership(
            workspace_id=workspace.id,
            user_id=member_user.id,
        )

    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.workspace_id == workspace.id,
            UserRoleAssignment.user_id == member_user.id,
        )
    )
    legacy = await session.scalar(
        select(LegacyMembership).where(
            LegacyMembership.workspace_id == workspace.id,
            LegacyMembership.user_id == member_user.id,
        )
    )

    assert assignment is not None
    assert legacy is not None


async def test_create_membership_keeps_the_user_an_org_member(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """A group-only org member gains the workspace without a direct org role."""
    await grant_org_membership_via_group(
        session, user_id=member_user.id, organization_id=organization.id
    )
    await session.commit()

    await membership_service.create_membership(
        workspace_id=workspace.id,
        params=WorkspaceMembershipCreate(user_id=member_user.id),
    )

    org_membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == member_user.id,
            OrganizationMembership.organization_id == organization.id,
        )
    )
    assert org_membership is not None


async def test_create_membership_rejects_user_outside_the_organization(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """A platform user with no role path in the org cannot be added.

    Workspace membership is organization presence, and an org member can be
    administered through the org member routes, so admitting an arbitrary user
    by ID would hand over their account.
    """
    with pytest.raises(TracecatNotFoundError, match="User not found in organization"):
        await membership_service.create_membership(
            workspace_id=workspace.id,
            params=WorkspaceMembershipCreate(user_id=member_user.id),
        )

    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.workspace_id == workspace.id,
            UserRoleAssignment.user_id == member_user.id,
        )
    )
    legacy = await session.scalar(
        select(LegacyMembership).where(
            LegacyMembership.workspace_id == workspace.id,
            LegacyMembership.user_id == member_user.id,
        )
    )
    assert assignment is None
    assert legacy is None


async def test_create_membership_rejects_member_of_another_organization(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Presence in a different organization does not admit the user here."""
    other_org = Organization(
        id=uuid.uuid4(),
        name="Other Org",
        slug=f"other-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(other_org)
    await session.flush()
    await grant_org_membership(
        session, user_id=member_user.id, organization_id=other_org.id
    )
    await session.commit()

    with pytest.raises(TracecatNotFoundError, match="User not found in organization"):
        await membership_service.create_membership(
            workspace_id=workspace.id,
            params=WorkspaceMembershipCreate(user_id=member_user.id),
        )

    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.workspace_id == workspace.id,
            UserRoleAssignment.user_id == member_user.id,
        )
    )
    legacy = await session.scalar(
        select(LegacyMembership).where(
            LegacyMembership.workspace_id == workspace.id,
            LegacyMembership.user_id == member_user.id,
        )
    )
    assert assignment is None
    assert legacy is None


# === update_membership_role === #


@pytest.fixture
async def workspace_viewer_role(
    session: AsyncSession, organization: Organization
) -> DBRole:
    """A workspace role with real viewer scopes, the target of a role change."""
    await seed_system_scopes(session)
    role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Viewer",
        slug="workspace-viewer",
        description="Viewer role",
        organization_id=organization.id,
    )
    session.add(role)
    await session.flush()
    result = await session.execute(
        select(Scope).where(Scope.name.in_(sorted(VIEWER_SCOPES)))
    )
    for scope in result.scalars().all():
        session.add(RoleScope(role_id=role.id, scope_id=scope.id))
    await session.commit()
    await session.refresh(role)
    return role


@pytest.fixture
async def workspace_admin_actor(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    actor_user: User,
) -> Role:
    """A caller whose only assignment is workspace-admin, with no org:rbac scopes."""
    await seed_system_scopes(session)
    admin_role = DBRole(
        id=uuid.uuid4(),
        name="WS Admin",
        slug=None,
        organization_id=organization.id,
    )
    session.add(admin_role)
    await session.flush()
    result = await session.execute(
        select(Scope).where(Scope.name.in_(sorted(ADMIN_SCOPES)))
    )
    for scope in result.scalars().all():
        session.add(RoleScope(role_id=admin_role.id, scope_id=scope.id))
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=actor_user.id,
            workspace_id=workspace.id,
            role_id=admin_role.id,
        )
    )
    await session.commit()
    assert not any(scope.startswith("org:rbac") for scope in ADMIN_SCOPES)
    return Role(
        type="user",
        user_id=actor_user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )


async def test_update_membership_role_changes_direct_assignment(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_admin_actor: Role,
    scoped_workspace_editor_role: DBRole,
    workspace_viewer_role: DBRole,
) -> None:
    """A workspace admin demotes an editor to viewer without any org scope."""
    await grant_workspace_membership(
        session,
        user_id=member_user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
        slug="workspace-editor",
    )
    await session.commit()
    service = MembershipService(session=session, role=workspace_admin_actor)

    await service.update_membership_role(
        workspace.id, user_id=member_user.id, role_id=workspace_viewer_role.id
    )

    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == member_user.id,
            UserRoleAssignment.workspace_id == workspace.id,
        )
    )
    assert assignment is not None
    assert assignment.role_id == workspace_viewer_role.id


async def test_update_membership_role_emits_audit_events(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_admin_actor: Role,
    workspace_viewer_role: DBRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace role change is auditable, like the org-level assignment change."""
    await grant_workspace_membership(
        session,
        user_id=member_user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
        slug="workspace-editor",
    )
    await session.commit()
    create_event = AsyncMock()
    monkeypatch.setattr(AuditService, "create_event", create_event)
    service = MembershipService(session=session, role=workspace_admin_actor)

    await service.update_membership_role(
        workspace.id, user_id=member_user.id, role_id=workspace_viewer_role.id
    )

    assert [call.kwargs["status"] for call in create_event.await_args_list] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.SUCCESS,
    ]
    attempt = create_event.await_args_list[0].kwargs
    assert attempt["resource_type"] == "rbac_user_assignment"
    assert attempt["action"] == "update"
    assert attempt["resource_id"] == member_user.id


async def test_update_membership_role_enforces_scope_ceiling(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_admin_actor: Role,
    scoped_workspace_editor_role: DBRole,
) -> None:
    """A role carrying a scope the caller lacks is refused."""
    await grant_workspace_membership(
        session,
        user_id=member_user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
        slug="workspace-editor",
    )
    privileged = DBRole(
        id=uuid.uuid4(),
        name="Privileged",
        slug=None,
        organization_id=organization.id,
    )
    session.add(privileged)
    await session.flush()
    owner_scope = await session.scalar(
        select(Scope).where(Scope.name == "org:owner:assign")
    )
    assert owner_scope is not None
    session.add(RoleScope(role_id=privileged.id, scope_id=owner_scope.id))
    await session.commit()
    service = MembershipService(session=session, role=workspace_admin_actor)

    with pytest.raises(
        TracecatAuthorizationError,
        match="Cannot grant scopes not held by the caller",
    ):
        await service.update_membership_role(
            workspace.id, user_id=member_user.id, role_id=privileged.id
        )


async def test_update_membership_role_rejects_org_level_role(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_admin_actor: Role,
    scoped_workspace_editor_role: DBRole,
) -> None:
    """An org-level role reaches every workspace, so it is refused here."""
    await grant_workspace_membership(
        session,
        user_id=member_user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
        slug="workspace-editor",
    )
    org_role = DBRole(
        id=uuid.uuid4(),
        name="Org Reader",
        slug=None,
        organization_id=organization.id,
    )
    session.add(org_role)
    await session.flush()
    org_scope = await session.scalar(select(Scope).where(Scope.name == "org:read"))
    assert org_scope is not None
    session.add(RoleScope(role_id=org_role.id, scope_id=org_scope.id))
    # Grant the caller the same scope so the rejection is the org-scope rule
    # and not the ceiling.
    caller_assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == workspace_admin_actor.user_id
        )
    )
    assert caller_assignment is not None
    session.add(RoleScope(role_id=caller_assignment.role_id, scope_id=org_scope.id))
    await session.commit()
    service = MembershipService(session=session, role=workspace_admin_actor)

    with pytest.raises(
        TracecatValidationError, match="Only workspace roles can be assigned here"
    ):
        await service.update_membership_role(
            workspace.id, user_id=member_user.id, role_id=org_role.id
        )


async def test_update_membership_role_conflicts_on_group_derived_member(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_admin_actor: Role,
    workspace_viewer_role: DBRole,
) -> None:
    """A member present only through a group cannot be changed here."""
    group = Group(id=uuid.uuid4(), name="Editors", organization_id=organization.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=member_user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=organization.id,
            group_id=group.id,
            workspace_id=workspace.id,
            role_id=workspace_viewer_role.id,
        )
    )
    await session.commit()
    service = MembershipService(session=session, role=workspace_admin_actor)

    with pytest.raises(TracecatConflictError, match="granted through a group"):
        await service.update_membership_role(
            workspace.id, user_id=member_user.id, role_id=workspace_viewer_role.id
        )


async def test_update_membership_role_not_found_for_non_member(
    session: AsyncSession,
    workspace: Workspace,
    member_user: User,
    workspace_admin_actor: Role,
    workspace_viewer_role: DBRole,
) -> None:
    """A user with no path into the workspace is not a member."""
    service = MembershipService(session=session, role=workspace_admin_actor)

    with pytest.raises(TracecatNotFoundError, match="Membership not found"):
        await service.update_membership_role(
            workspace.id, user_id=member_user.id, role_id=workspace_viewer_role.id
        )


async def test_create_membership_reads_org_presence_through_bypass_session(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
    actor_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Organization presence is read through the RLS-bypass session.

    Under enforced RLS the assignment tables hide rows keyed to another
    workspace, so reading presence on the request session would report a user
    whose only role path is in a different workspace as absent.
    """
    other_workspace = Workspace(
        id=uuid.uuid4(), name="Other Workspace", organization_id=organization.id
    )
    session.add(other_workspace)
    await grant_workspace_membership(
        session,
        user_id=member_user.id,
        organization_id=organization.id,
        workspace_id=other_workspace.id,
    )
    await session.commit()

    used = False

    @contextlib.asynccontextmanager
    async def _bypass() -> AsyncIterator[AsyncSession]:
        nonlocal used
        used = True
        yield session

    monkeypatch.setattr(
        authz_service, "get_async_session_bypass_rls_context_manager", _bypass
    )
    service = MembershipService(session=session, role=actor_role)

    await service.create_membership(
        workspace.id, WorkspaceMembershipCreate(user_id=member_user.id)
    )

    assert used, "org presence must be read through the RLS-bypass session"
    assignment = (
        await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == member_user.id,
                UserRoleAssignment.workspace_id == workspace.id,
            )
        )
    ).scalar_one()
    assert assignment.role_id == workspace_editor_role.id
