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
    Membership,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatValidationError
from tracecat.workspaces.schemas import (
    WorkspaceMembershipBulkCreate,
    WorkspaceMembershipCreate,
)

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


async def test_delete_membership_does_not_require_default_workspace_role(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
) -> None:
    """Delete should still work when the default workspace role is missing."""
    custom_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Admin",
        slug="workspace-admin",
        description="Custom workspace role",
        organization_id=organization.id,
    )
    session.add(
        Membership(
            user_id=member_user.id,
            workspace_id=workspace.id,
        )
    )
    session.add(custom_role)
    session.add(
        UserRoleAssignment(
            organization_id=organization.id,
            user_id=member_user.id,
            workspace_id=workspace.id,
            role_id=custom_role.id,
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


async def test_create_membership_duplicate_raises_validation_error(
    session: AsyncSession,
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Creating an existing membership should fail before insert."""
    assert workspace_editor_role.slug == "workspace-editor"
    session.add(
        Membership(
            user_id=member_user.id,
            workspace_id=workspace.id,
        )
    )
    await session.commit()

    with pytest.raises(
        TracecatValidationError,
        match="User is already a member of workspace.",
    ):
        await membership_service.create_membership(
            workspace_id=workspace.id,
            params=WorkspaceMembershipCreate(user_id=member_user.id),
        )


async def test_create_memberships_bulk_creates_memberships_and_assignments(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Bulk create should grant workspace access to existing org members."""
    user_one = User(
        id=uuid.uuid4(),
        email=f"bulk-one-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    user_two = User(
        id=uuid.uuid4(),
        email=f"bulk-two-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=False,
        is_superuser=False,
        is_verified=True,
    )
    session.add_all([user_one, user_two])
    await session.flush()
    session.add_all(
        [
            OrganizationMembership(
                organization_id=organization.id, user_id=user_one.id
            ),
            OrganizationMembership(
                organization_id=organization.id, user_id=user_two.id
            ),
        ]
    )
    await session.commit()

    processed_count = await membership_service.create_memberships_bulk(
        workspace_id=workspace.id,
        params=WorkspaceMembershipBulkCreate(
            user_ids=[user_one.id, user_two.id],
            role_id=workspace_editor_role.id,
        ),
    )

    memberships = (
        (
            await session.execute(
                select(Membership).where(Membership.workspace_id == workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assignments = (
        (
            await session.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.workspace_id == workspace.id,
                    UserRoleAssignment.role_id == workspace_editor_role.id,
                )
            )
        )
        .scalars()
        .all()
    )

    assert processed_count == 2
    assert {membership.user_id for membership in memberships} == {
        user_one.id,
        user_two.id,
    }
    assert {assignment.user_id for assignment in assignments} == {
        user_one.id,
        user_two.id,
    }
    assert all(assignment.assigned_by == actor_user.id for assignment in assignments)


async def test_create_memberships_bulk_updates_existing_member_role(
    session: AsyncSession,
    membership_service: MembershipService,
    organization: Organization,
    workspace: Workspace,
    member_user: User,
    actor_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Bulk create should update direct workspace role assignments idempotently."""
    workspace_admin_role = DBRole(
        id=uuid.uuid4(),
        name="Workspace Admin",
        slug="workspace-admin",
        description="Admin role",
        organization_id=organization.id,
    )
    session.add(workspace_admin_role)
    session.add(
        OrganizationMembership(organization_id=organization.id, user_id=member_user.id)
    )
    session.add(Membership(user_id=member_user.id, workspace_id=workspace.id))
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

    processed_count = await membership_service.create_memberships_bulk(
        workspace_id=workspace.id,
        params=WorkspaceMembershipBulkCreate(
            user_ids=[member_user.id],
            role_id=workspace_admin_role.id,
        ),
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
    assignment = await session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.workspace_id == workspace.id,
            UserRoleAssignment.user_id == member_user.id,
        )
    )

    assert processed_count == 1
    assert len(memberships) == 1
    assert assignment is not None
    assert assignment.role_id == workspace_admin_role.id


async def test_create_memberships_bulk_rejects_non_org_members(
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Bulk create should reject users who are not already org members."""
    with pytest.raises(
        TracecatValidationError,
        match="Selected users must already be members of this organization",
    ):
        await membership_service.create_memberships_bulk(
            workspace_id=workspace.id,
            params=WorkspaceMembershipBulkCreate(
                user_ids=[member_user.id],
                role_id=workspace_editor_role.id,
            ),
        )
