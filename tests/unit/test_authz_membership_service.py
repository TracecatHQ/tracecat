"""Unit tests for MembershipService."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.authz.service import MembershipService
from tracecat.db.models import (
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


async def test_create_membership_duplicate_raises_integrity_error(
    session: AsyncSession,
    membership_service: MembershipService,
    workspace: Workspace,
    member_user: User,
    workspace_editor_role: DBRole,
) -> None:
    """Creating an existing membership should raise an integrity conflict."""
    assert workspace_editor_role.slug == "workspace-editor"
    session.add(
        Membership(
            user_id=member_user.id,
            workspace_id=workspace.id,
        )
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await membership_service.create_membership(
            workspace_id=workspace.id,
            params=WorkspaceMembershipCreate(user_id=member_user.id),
        )
