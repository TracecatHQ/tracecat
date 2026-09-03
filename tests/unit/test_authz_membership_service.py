"""Unit tests for MembershipService."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.membership import resolve_org_role_names
from tracecat.authz.scopes import ADMIN_SCOPES, EDITOR_SCOPES
from tracecat.authz.seeding import seed_system_scopes
from tracecat.authz.service import MembershipService
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    Membership,
    Organization,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    GroupDerivedMembershipError,
    TracecatAuthorizationError,
)
from tracecat.workspaces.schemas import WorkspaceMembershipCreate

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


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

    assert membership is None
    assert assignment is None


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


async def test_create_membership_heals_stale_workspace_assignment(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Create should succeed when only a stale workspace assignment exists."""
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

    await membership_service.create_membership(
        workspace_id=workspace.id,
        params=WorkspaceMembershipCreate(user_id=member_user.id),
    )

    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == member_user.id,
        )
    )
    assignments = (
        await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace.id,
                UserRoleAssignment.user_id == member_user.id,
            )
        )
    ).scalars()
    assignment_list = list(assignments)

    assert membership is not None
    assert len(assignment_list) == 1
    assert assignment_list[0].organization_id == organization.id
    assert assignment_list[0].role_id == workspace_editor_role.id
    assert assignment_list[0].assigned_by == actor_user.id


async def test_create_membership_is_idempotent(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Re-adding an existing member replaces the assignment rather than failing."""
    assert workspace_editor_role.slug == "workspace-editor"
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
        )
    )
    await session.commit()

    await membership_service.create_membership(
        workspace_id=workspace.id,
        params=WorkspaceMembershipCreate(user_id=member_user.id),
    )

    assignments = (
        await session.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace.id,
                UserRoleAssignment.user_id == member_user.id,
            )
        )
    ).scalars()
    assert len(list(assignments)) == 1


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


@pytest.fixture
async def granting_group(
    session: AsyncSession,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> Group:
    """A group that grants member_user access to the workspace."""
    group = Group(
        id=uuid.uuid4(),
        name=f"engineering-{uuid.uuid4().hex[:8]}",
        organization_id=organization.id,
    )
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=member_user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=organization.id,
            group_id=group.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
        )
    )
    await session.commit()
    await session.refresh(group)
    return group


async def test_delete_membership_rejects_group_derived_member(
    session: AsyncSession,
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    granting_group: Group,
) -> None:
    """A member whose access is only group-derived cannot be removed directly."""
    with pytest.raises(GroupDerivedMembershipError) as excinfo:
        await membership_service.delete_membership(
            workspace_id=workspace.id,
            user_id=member_user.id,
        )

    assert excinfo.value.group_names == [granting_group.name]
    assert excinfo.value.detail == {
        "code": "group_derived_membership",
        "group_names": [granting_group.name],
    }

    # The preflight must not have mutated anything: the member is still there.
    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == member_user.id,
        )
    )
    assert membership is not None


async def test_delete_membership_removes_direct_assignment_despite_group(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
    granting_group: Group,
) -> None:
    """A direct assignment is deleted even when a group also grants access."""
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


async def test_list_workspace_members_reports_direct_source(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """A directly assigned member reports source kind 'direct'."""
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

    members = await membership_service.list_workspace_members(workspace.id)

    member = next(m for m in members if m.user_id == member_user.id)
    assert member.source.kind == "direct"
    assert member.source.group_names == []


async def test_list_workspace_members_reports_group_source(
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    granting_group: Group,
) -> None:
    """A group-derived member reports the granting group names."""
    members = await membership_service.list_workspace_members(workspace.id)

    member = next(m for m in members if m.user_id == member_user.id)
    assert member.source.kind == "group"
    assert member.source.group_names == [granting_group.name]


@pytest.fixture
async def workspace_viewer_role(
    session: AsyncSession, organization: Organization
) -> DBRole:
    """A non-editor workspace role for group-granted access."""
    role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Viewer",
        slug="workspace-viewer",
        description="Read-only role",
        organization_id=organization.id,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


async def test_list_workspace_members_reports_group_granted_role_name(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_viewer_role: DBRole,
) -> None:
    """A group-only member shows the role the group grants, not the default."""
    group = Group(
        id=uuid.uuid4(),
        name=f"viewers-{uuid.uuid4().hex[:8]}",
        organization_id=organization.id,
    )
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

    members = await membership_service.list_workspace_members(workspace.id)

    member = next(m for m in members if m.user_id == member_user.id)
    assert member.source.kind == "group"
    assert member.source.group_names == [group.name]
    assert member.role_name == "Workspace Viewer"


async def test_list_workspace_members_picks_first_role_name_across_groups(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    granting_group: Group,
    workspace_viewer_role: DBRole,
) -> None:
    """With several granting groups, the first role name alphabetically wins."""
    group = Group(
        id=uuid.uuid4(),
        name=f"viewers-{uuid.uuid4().hex[:8]}",
        organization_id=organization.id,
    )
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

    members = await membership_service.list_workspace_members(workspace.id)

    member = next(m for m in members if m.user_id == member_user.id)
    # "Workspace Editor" (from granting_group) sorts before "Workspace Viewer".
    assert member.role_name == "Workspace Editor"
    assert sorted(member.source.group_names) == sorted(
        [granting_group.name, group.name]
    )


@pytest.fixture
async def org_admin_role(session: AsyncSession, organization: Organization) -> DBRole:
    """An org-wide admin role."""
    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Admin",
        slug="organization-admin",
        description="Org admin",
        organization_id=organization.id,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


async def test_resolve_org_role_names_resolves_via_group(
    session: AsyncSession,
    organization: Organization,
    member_user: User,
    org_admin_role: DBRole,
) -> None:
    """An org role granted through an org-wide group assignment resolves."""
    group = Group(
        id=uuid.uuid4(),
        name=f"admins-{uuid.uuid4().hex[:8]}",
        organization_id=organization.id,
    )
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=member_user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=organization.id,
            group_id=group.id,
            workspace_id=None,
            role_id=org_admin_role.id,
        )
    )
    await session.commit()

    resolved = await resolve_org_role_names(session, organization.id, [member_user.id])

    assert resolved[member_user.id].name == "Organization Admin"
    assert resolved[member_user.id].slug == "organization-admin"


async def test_resolve_org_role_names_prefers_direct_assignment(
    session: AsyncSession,
    organization: Organization,
    member_user: User,
    actor_user: User,
    org_admin_role: DBRole,
    workspace_editor_role: DBRole,
) -> None:
    """A direct org assignment wins over a group-derived one."""
    group = Group(
        id=uuid.uuid4(),
        name=f"admins-{uuid.uuid4().hex[:8]}",
        organization_id=organization.id,
    )
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=member_user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=organization.id,
            group_id=group.id,
            workspace_id=None,
            role_id=org_admin_role.id,
        )
    )
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=None,
            role_id=workspace_editor_role.id,
            assigned_by=actor_user.id,
        )
    )
    await session.commit()

    resolved = await resolve_org_role_names(session, organization.id, [member_user.id])

    assert resolved[member_user.id].name == "Workspace Editor"


async def test_resolve_org_role_names_ignores_workspace_scoped_grants(
    session: AsyncSession,
    organization: Organization,
    member_user: User,
    workspace: Workspace,
    granting_group: Group,
) -> None:
    """A workspace-scoped group grant is not an org role."""
    resolved = await resolve_org_role_names(session, organization.id, [member_user.id])

    assert member_user.id not in resolved
