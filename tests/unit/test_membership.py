"""Tests for membership derived from RBAC role assignments.

Membership is a SQLAlchemy subquery over user and group role assignments. A
NULL workspace_id is organization presence; a non-NULL value is workspace
presence. Org-wide roles never produce workspace rows.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.membership import grant_org_membership, grant_workspace_membership
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.authz.seeding import seed_system_roles_for_org
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
from tracecat.organization.service import OrgService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(
        id=org_id,
        name="Membership Org",
        slug=f"membership-{org_id.hex[:8]}",
    )
    session.add(org)
    await session.flush()
    await seed_system_roles_for_org(session, org.id)
    await session.commit()
    return org


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization) -> Workspace:
    workspace = Workspace(id=uuid.uuid4(), name="Workspace One", organization_id=org.id)
    session.add(workspace)
    await session.commit()
    return workspace


@pytest.fixture
async def other_workspace(session: AsyncSession, org: Organization) -> Workspace:
    workspace = Workspace(id=uuid.uuid4(), name="Workspace Two", organization_id=org.id)
    session.add(workspace)
    await session.commit()
    return workspace


async def _make_user(session: AsyncSession) -> User:
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
    await session.flush()
    return user


async def _membership_rows(
    session: AsyncSession, user_id: uuid.UUID
) -> list[Membership]:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user_id)
    )
    return list(result.scalars().all())


@pytest.mark.anyio
async def test_org_wide_role_grants_org_membership_only(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> None:
    """An org-wide role makes the user an org member but not a workspace member."""
    user = await _make_user(session)
    await grant_org_membership(session, user_id=user.id, organization_id=org.id)
    await session.commit()

    rows = await _membership_rows(session, user.id)

    assert len(rows) == 1
    assert rows[0].organization_id == org.id
    assert rows[0].workspace_id is None


@pytest.mark.anyio
async def test_workspace_scoped_role_grants_workspace_and_org_membership(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> None:
    """A workspace-scoped role yields both an org row and a workspace row."""
    user = await _make_user(session)
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
    )
    await session.commit()

    rows = await _membership_rows(session, user.id)

    assert {row.workspace_id for row in rows} == {None, workspace.id}
    assert {row.organization_id for row in rows} == {org.id}


@pytest.mark.anyio
async def test_group_workspace_assignment_grants_membership_to_group_members(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> None:
    """Group members inherit workspace membership, and lose it when it is revoked."""
    user = await _make_user(session)
    group = Group(id=uuid.uuid4(), name="Engineering", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(user_id=user.id, group_id=group.id))

    editor_role_id = (
        await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == org.id,
                DBRole.slug == "workspace-editor",
            )
        )
    ).scalar_one()
    assignment = GroupRoleAssignment(
        organization_id=org.id,
        group_id=group.id,
        workspace_id=workspace.id,
        role_id=editor_role_id,
    )
    session.add(assignment)
    await session.commit()

    rows = await _membership_rows(session, user.id)
    assert {row.workspace_id for row in rows} == {None, workspace.id}

    # Revoking the group's assignment delists every group member.
    await session.execute(
        delete(GroupRoleAssignment).where(GroupRoleAssignment.id == assignment.id)
    )
    await session.commit()

    assert await _membership_rows(session, user.id) == []


@pytest.mark.anyio
async def test_deleting_last_assignment_removes_org_membership(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> None:
    """Membership disappears once the user's last assignment is gone."""
    user = await _make_user(session)
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
    )
    await session.commit()
    assert await _membership_rows(session, user.id)

    await session.execute(
        delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
    )
    await session.commit()

    assert await _membership_rows(session, user.id) == []


@pytest.mark.anyio
async def test_org_and_workspace_assignments_do_not_duplicate_org_row(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> None:
    """A user holding both assignment kinds still has exactly one org row."""
    user = await _make_user(session)
    await grant_org_membership(session, user_id=user.id, organization_id=org.id)
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
    )
    await session.commit()

    rows = await _membership_rows(session, user.id)

    assert len([row for row in rows if row.workspace_id is None]) == 1
    assert len(rows) == 2


@pytest.mark.anyio
async def test_user_in_two_workspaces_has_single_org_row(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    """Membership in two workspaces still collapses to one org-presence row."""
    user = await _make_user(session)
    for ws in (workspace, other_workspace):
        await grant_workspace_membership(
            session,
            user_id=user.id,
            organization_id=org.id,
            workspace_id=ws.id,
        )
    await session.commit()

    rows = await _membership_rows(session, user.id)

    assert len(rows) == 3
    assert [row.workspace_id for row in rows].count(None) == 1
    assert {row.workspace_id for row in rows if row.workspace_id is not None} == {
        workspace.id,
        other_workspace.id,
    }


@pytest.mark.anyio
async def test_org_members_list_returns_user_once_for_two_workspace_roles(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    user = await _make_user(session)
    for ws in (workspace, other_workspace):
        await grant_workspace_membership(
            session,
            user_id=user.id,
            organization_id=org.id,
            workspace_id=ws.id,
        )
    await session.commit()

    service = OrgService(
        session,
        role=Role(
            type="user",
            user_id=user.id,
            organization_id=org.id,
            service_id="tracecat-api",
            scopes=ORG_ADMIN_SCOPES,
        ),
    )

    members = await service.list_members()

    assert [member.id for member in members] == [user.id]


@pytest.mark.anyio
async def test_membership_rows_are_scoped_to_their_own_organization(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> None:
    """An assignment in one org never produces membership rows for another."""
    other_org = Organization(
        id=uuid.uuid4(),
        name="Other Org",
        slug=f"other-org-{uuid.uuid4().hex[:8]}",
    )
    session.add(other_org)
    await session.flush()
    await seed_system_roles_for_org(session, other_org.id)

    user = await _make_user(session)
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
    )
    await session.commit()

    rows = await _membership_rows(session, user.id)

    assert {row.organization_id for row in rows} == {org.id}


@pytest.mark.anyio
async def test_group_org_wide_assignment_grants_org_membership_only(
    session: AsyncSession, org: Organization, workspace: Workspace
) -> None:
    """An org-wide group role gives members org presence but no workspace row."""
    user = await _make_user(session)
    group = Group(id=uuid.uuid4(), name="Org Wide", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(user_id=user.id, group_id=group.id))

    role_id = (
        await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == org.id,
                DBRole.slug == "organization-member",
            )
        )
    ).scalar_one()
    session.add(
        GroupRoleAssignment(
            organization_id=org.id,
            group_id=group.id,
            workspace_id=None,
            role_id=role_id,
        )
    )
    await session.commit()

    rows = await _membership_rows(session, user.id)

    assert len(rows) == 1
    assert rows[0].workspace_id is None
