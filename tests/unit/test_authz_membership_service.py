"""Unit tests for MembershipService."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.authz.service import MembershipService
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    Membership,
    Organization,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
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
        Membership(
            user_id=member_user.id,
            workspace_id=workspace.id,
        )
    )
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


async def test_create_membership_is_idempotent_with_existing_default_assignment(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Re-adding a member who already holds the default role is a no-op."""
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


async def test_create_membership_preserves_existing_custom_role(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Re-adding a member must not downgrade a pre-existing non-default role.

    A duplicate/retried create_membership for a user who already holds a direct
    workspace-scoped role (e.g. a custom admin role) must preserve that
    assignment rather than overwriting it with the default workspace-editor
    role.
    """
    admin_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Admin",
        slug="workspace-admin",
        description="Custom admin role",
        organization_id=organization.id,
    )
    session.add(admin_role)
    await session.flush()
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=workspace.id,
            role_id=admin_role.id,
            assigned_by=actor_user.id,
        )
    )
    await session.commit()

    await membership_service.create_membership(
        workspace_id=workspace.id,
        params=WorkspaceMembershipCreate(user_id=member_user.id),
    )

    assignment_list = list(
        (
            await session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.workspace_id == workspace.id,
                    UserRoleAssignment.user_id == member_user.id,
                )
            )
        ).scalars()
    )
    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == member_user.id,
        )
    )

    assert membership is not None
    assert len(assignment_list) == 1
    # The custom admin role is preserved, not downgraded to workspace-editor.
    assert assignment_list[0].role_id == admin_role.id


async def test_create_membership_is_idempotent_when_membership_exists(
    session: AsyncSession,
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Re-adding an existing member is idempotent under the reconciler.

    The membership dial is now derived from the workspace-scoped role path via
    ``reconcile_workspace_membership`` (ENG-1499 / B1), which upserts with
    ``ON CONFLICT DO NOTHING``. Calling create again writes/heals the role
    assignment and leaves a single membership row, rather than raising.
    """
    assert workspace_editor_role.slug == "workspace-editor"
    session.add(
        Membership(
            user_id=member_user.id,
            workspace_id=workspace.id,
        )
    )
    await session.commit()

    await membership_service.create_membership(
        workspace_id=workspace.id,
        params=WorkspaceMembershipCreate(user_id=member_user.id),
    )

    memberships = (
        (
            await session.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace.id,
                    Membership.user_id == member_user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(memberships) == 1


# === reconcile_workspace_membership (ENG-1499 / B1) === #


async def _get_membership(
    session: AsyncSession, workspace: Workspace, user: User
) -> Membership | None:
    return await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == user.id,
        )
    )


@pytest.fixture
async def group(session: AsyncSession, organization: Organization) -> Group:
    grp = Group(
        id=uuid.uuid4(),
        name=f"group-{uuid.uuid4().hex[:8]}",
        organization_id=organization.id,
    )
    session.add(grp)
    await session.commit()
    await session.refresh(grp)
    return grp


async def test_reconcile_materializes_membership_for_direct_ws_path(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """A direct workspace-scoped role assignment should mint a membership row."""
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
        )
    )
    await session.commit()

    await membership_service.reconcile_workspace_membership(
        member_user.id, workspace.id
    )

    assert await _get_membership(session, workspace, member_user) is not None


async def test_reconcile_org_wide_path_does_not_materialize(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Org-wide (workspace_id IS NULL) assignments must NOT create membership."""
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=None,
            role_id=workspace_editor_role.id,
        )
    )
    await session.commit()

    await membership_service.reconcile_workspace_membership(
        member_user.id, workspace.id
    )

    assert await _get_membership(session, workspace, member_user) is None


async def test_reconcile_is_idempotent(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Reconciling twice yields exactly one membership row."""
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
        )
    )
    await session.commit()

    await membership_service.reconcile_workspace_membership(
        member_user.id, workspace.id
    )
    await membership_service.reconcile_workspace_membership(
        member_user.id, workspace.id
    )

    rows = (
        (
            await session.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace.id,
                    Membership.user_id == member_user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_reconcile_removes_membership_when_no_path_remains(
    session: AsyncSession,
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
) -> None:
    """A membership with no backing ws-scoped path is reconciled away."""
    session.add(Membership(user_id=member_user.id, workspace_id=workspace.id))
    await session.commit()

    await membership_service.reconcile_workspace_membership(
        member_user.id, workspace.id
    )

    assert await _get_membership(session, workspace, member_user) is None


async def test_reconcile_materializes_membership_for_group_ws_path(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
    group: Group,
) -> None:
    """Membership in a group with a ws-scoped assignment mints membership."""
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

    await membership_service.reconcile_workspace_membership(
        member_user.id, workspace.id
    )

    assert await _get_membership(session, workspace, member_user) is not None


async def test_reconcile_keeps_membership_when_group_path_remains(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
    group: Group,
) -> None:
    """Removing a direct assignment keeps membership if a group path holds it."""
    session.add(GroupMember(group_id=group.id, user_id=member_user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=organization.id,
            group_id=group.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
        )
    )
    session.add(Membership(user_id=member_user.id, workspace_id=workspace.id))
    await session.commit()

    # No direct assignment exists, but the group path does -> membership stays.
    await membership_service.reconcile_workspace_membership(
        member_user.id, workspace.id
    )

    assert await _get_membership(session, workspace, member_user) is not None


async def test_reconcile_group_members_fans_out(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
    group: Group,
) -> None:
    """reconcile_group_members materializes membership for every group member."""
    session.add(GroupMember(group_id=group.id, user_id=member_user.id))
    session.add(GroupMember(group_id=group.id, user_id=actor_user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=organization.id,
            group_id=group.id,
            workspace_id=workspace.id,
            role_id=workspace_editor_role.id,
        )
    )
    await session.commit()

    await membership_service.reconcile_group_members(group.id, workspace.id)

    assert await _get_membership(session, workspace, member_user) is not None
    assert await _get_membership(session, workspace, actor_user) is not None
