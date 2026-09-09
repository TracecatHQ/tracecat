"""Tests for membership derived from role assignments.

``Membership`` and ``OrganizationMembership`` are read-only relations the ORM
derives from the role-assignment tables (see ``tracecat.db.models``). The
legacy physical tables stay in place but are never read.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    LegacyMembership,
    LegacyOrganizationMembership,
    Membership,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    """Create a test organization."""
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization) -> Workspace:
    """Create a test workspace."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def other_workspace(session: AsyncSession, org: Organization) -> Workspace:
    """A second workspace, for group-granted presence."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Other Workspace",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def user(session: AsyncSession) -> User:
    """Create a user holding no role path at all."""
    user = User(
        id=uuid.uuid4(),
        email=f"derived-{uuid.uuid4().hex[:8]}@example.com",
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
        name=f"derived-{uuid.uuid4().hex[:8]}",
        slug=None,
        description=None,
        organization_id=org.id,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


async def _presence(session: AsyncSession, user_id: uuid.UUID) -> tuple[int, int]:
    """(workspace rows, org rows) the ORM derives for a user."""
    workspaces = (
        await session.execute(select(Membership).where(Membership.user_id == user_id))
    ).scalars()
    orgs = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id
            )
        )
    ).scalars()
    return len(list(workspaces)), len(list(orgs))


async def test_derives_presence_from_assignment_paths(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    other_workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """Each path kind lands in exactly the relation it should."""
    assert await _presence(session, user.id) == (0, 0)

    # A workspace-scoped assignment is workspace presence only.
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=workspace.id,
            role_id=db_role.id,
        )
    )
    await session.flush()
    assert await _presence(session, user.id) == (1, 0)

    # An org-wide assignment is org presence only.
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=None,
            role_id=db_role.id,
        )
    )
    await session.flush()
    assert await _presence(session, user.id) == (1, 1)

    # A group grant reaches its members.
    group = Group(id=uuid.uuid4(), name="Derived Group", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=org.id,
            group_id=group.id,
            workspace_id=other_workspace.id,
            role_id=db_role.id,
        )
    )
    await session.flush()
    assert await _presence(session, user.id) == (2, 1)


async def test_org_slice_is_the_null_workspace_path(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """A workspace-scoped path alone is never organization presence."""
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=workspace.id,
            role_id=db_role.id,
        )
    )
    await session.flush()
    assert await _presence(session, user.id) == (1, 0)

    # A group's org-wide grant is organization presence, with no workspace row.
    group = Group(id=uuid.uuid4(), name="Org Wide Group", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=user.id))
    session.add(
        GroupRoleAssignment(
            organization_id=org.id,
            group_id=group.id,
            workspace_id=None,
            role_id=db_role.id,
        )
    )
    await session.flush()
    assert await _presence(session, user.id) == (1, 1)


async def test_orm_never_reads_the_legacy_tables(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
) -> None:
    """A legacy row with no assignment is invisible: the tables are unread."""
    for statement in (select(Membership), select(OrganizationMembership)):
        compiled = str(statement.compile())
        assert (
            re.search(r"\bFROM (membership|organization_membership)\b", compiled)
            is None
        )

    session.add(LegacyMembership(user_id=user.id, workspace_id=workspace.id))
    session.add(LegacyOrganizationMembership(user_id=user.id, organization_id=org.id))
    await session.flush()

    assert await _presence(session, user.id) == (0, 0)
