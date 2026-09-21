"""Tests for the derived workspace-presence existence check.

``workspace_membership_exists`` answers presence from the role-path union, so
every arm that grants a path must grant access and nothing else may.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.membership import ensure_member
from tracecat.authz.service import workspace_membership_exists
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    Organization,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    """An organization owning the workspaces under test."""
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Exists Org", slug=f"exists-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def other_org(session: AsyncSession) -> Organization:
    """A second organization, for cross-tenant denial."""
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Other Org", slug=f"other-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization) -> Workspace:
    """The workspace access is checked against."""
    workspace = Workspace(
        id=uuid.uuid4(), name="Target Workspace", organization_id=org.id
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def other_workspace(session: AsyncSession, org: Organization) -> Workspace:
    """A sibling workspace in the same organization."""
    workspace = Workspace(
        id=uuid.uuid4(), name="Other Workspace", organization_id=org.id
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def user(session: AsyncSession) -> User:
    """A user holding no role path at all."""
    user = User(
        id=uuid.uuid4(),
        email=f"exists-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def db_role(session: AsyncSession, org: Organization) -> DBRole:
    """A scopeless role, enough to form a path."""
    role = DBRole(
        name=f"exists-{uuid.uuid4().hex[:8]}",
        slug=None,
        description=None,
        organization_id=org.id,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


async def test_direct_role_grants_access(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """A workspace-scoped user role assignment is presence."""
    await ensure_member(session, org.id, user.id)
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=workspace.id,
            role_id=db_role.id,
        )
    )
    await session.flush()

    assert await workspace_membership_exists(
        session, user_id=user.id, workspace_id=workspace.id
    )


async def test_group_path_grants_access(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """A group grant reaches its members."""
    await ensure_member(session, org.id, user.id)
    group = Group(id=uuid.uuid4(), name="Exists Group", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=user.id, organization_id=org.id))
    session.add(
        GroupRoleAssignment(
            organization_id=org.id,
            group_id=group.id,
            workspace_id=workspace.id,
            role_id=db_role.id,
        )
    )
    await session.flush()

    assert await workspace_membership_exists(
        session, user_id=user.id, workspace_id=workspace.id
    )


async def test_no_path_is_denied(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
) -> None:
    """Org membership alone is not workspace presence."""
    await ensure_member(session, org.id, user.id)
    await session.flush()

    assert not await workspace_membership_exists(
        session, user_id=user.id, workspace_id=workspace.id
    )


async def test_path_in_another_workspace_is_denied(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    other_workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """A grant elsewhere does not reach the target workspace."""
    await ensure_member(session, org.id, user.id)
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=other_workspace.id,
            role_id=db_role.id,
        )
    )
    await session.flush()

    assert not await workspace_membership_exists(
        session, user_id=user.id, workspace_id=workspace.id
    )


async def test_user_in_another_org_is_denied(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    workspace: Workspace,
    user: User,
) -> None:
    """A grant in another tenant's workspace does not reach this one."""
    foreign_workspace = Workspace(
        id=uuid.uuid4(), name="Foreign Workspace", organization_id=other_org.id
    )
    foreign_role = DBRole(
        name=f"foreign-{uuid.uuid4().hex[:8]}",
        slug=None,
        description=None,
        organization_id=other_org.id,
    )
    session.add_all([foreign_workspace, foreign_role])
    await session.flush()
    await ensure_member(session, other_org.id, user.id)
    session.add(
        UserRoleAssignment(
            organization_id=other_org.id,
            user_id=user.id,
            workspace_id=foreign_workspace.id,
            role_id=foreign_role.id,
        )
    )
    await session.flush()

    assert not await workspace_membership_exists(
        session, user_id=user.id, workspace_id=workspace.id
    )
