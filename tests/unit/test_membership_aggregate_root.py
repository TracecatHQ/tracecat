"""Tests for organization membership as the aggregate root.

``OrganizationMembership`` is stored: children hang off it by composite foreign
key, so one DELETE unwinds them. Workspace ``Membership`` stays derived from the
role-assignment tables (see ``tracecat.db.models``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import Insert, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.authz.membership import ensure_member, mirror_workspace_membership
from tracecat.db.models import (
    Group,
    GroupMember,
    GroupRoleAssignment,
    MCPPersonalAccessToken,
    MCPRefreshToken,
    Membership,
    Organization,
    OrganizationMembership,
    ServiceAccount,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.db.rls import (
    RLS_BYPASS_OFF,
    RLS_BYPASS_ON,
    RLS_VAR_BYPASS,
    set_rls_context,
)

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


async def test_workspace_presence_stays_derived(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    other_workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """Workspace rows follow the role paths; org rows follow the stored row."""
    await ensure_member(session, org.id, user.id)
    await session.flush()
    assert await _presence(session, user.id) == (0, 1)

    # A workspace-scoped assignment is workspace presence.
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=workspace.id,
            role_id=db_role.id,
        )
    )
    await session.flush()
    assert await _presence(session, user.id) == (1, 1)

    # A group grant reaches its members.
    group = Group(id=uuid.uuid4(), name="Derived Group", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=user.id, organization_id=org.id))
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


async def test_org_presence_is_stored_not_derived(
    session: AsyncSession,
    org: Organization,
    user: User,
) -> None:
    """The row alone is presence: no assignment is required to hold it."""
    await ensure_member(session, org.id, user.id)
    await session.flush()
    assert await _presence(session, user.id) == (0, 1)

    # ensure_member is idempotent.
    await ensure_member(session, org.id, user.id)
    await session.flush()
    assert await _presence(session, user.id) == (0, 1)


async def test_assignment_without_membership_is_rejected(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """The composite foreign key refuses a child with no aggregate root."""
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=workspace.id,
            role_id=db_role.id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_losing_the_last_assignment_keeps_the_member(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """Membership is explicit: dropping the last role path is not a removal."""
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
    assert await _presence(session, user.id) == (1, 1)

    await session.execute(
        delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
    )
    await session.flush()

    # No role path left, but the row stands: only Remove member deletes it.
    assert await _presence(session, user.id) == (0, 1)


async def test_deleting_membership_unwinds_children(
    session: AsyncSession,
    org: Organization,
    workspace: Workspace,
    user: User,
    db_role: DBRole,
) -> None:
    """One DELETE cascades children, nulls the owner and revokes MCP tokens."""
    await ensure_member(session, org.id, user.id)
    await session.flush()

    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=workspace.id,
            role_id=db_role.id,
        )
    )
    group = Group(id=uuid.uuid4(), name="Cascade Group", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMember(group_id=group.id, user_id=user.id, organization_id=org.id))

    service_account = ServiceAccount(
        id=uuid.uuid4(),
        organization_id=org.id,
        name=f"sa-{uuid.uuid4().hex[:8]}",
        owner_user_id=user.id,
    )
    pat = MCPPersonalAccessToken(
        id=uuid.uuid4(),
        user_id=user.id,
        organization_id=org.id,
        name="pat",
        key_id=uuid.uuid4().hex[:16],
        hashed="hashed",
        salt="salt",
        preview="preview",
    )
    refresh_token = MCPRefreshToken(
        id=uuid.uuid4(),
        token_hash=uuid.uuid4().hex,
        family_id=uuid.uuid4(),
        user_id=user.id,
        organization_id=org.id,
        client_id="client",
        encrypted_metadata=b"\x00",
        status="active",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    actor = User(
        id=uuid.uuid4(),
        email=f"actor-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
    )
    session.add_all([service_account, pat, refresh_token, actor])
    await session.commit()

    user_id = user.id
    org_id = org.id
    service_account_id = service_account.id
    pat_id = pat.id
    refresh_token_id = refresh_token.id
    actor_id = actor.id

    # The trigger attributes the revocation to the caller's RLS user setting.
    await set_rls_context(session, org_id=org_id, workspace_id=None, user_id=actor_id)
    await session.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == org_id,
        )
    )
    await session.commit()
    # The DELETE ran in SQL, so drop identity-map copies of the affected rows.
    session.expunge_all()

    assert await _presence(session, user_id) == (0, 0)
    assignments = (
        await session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id)
        )
    ).scalars()
    assert list(assignments) == []
    group_members = (
        await session.execute(select(GroupMember).where(GroupMember.user_id == user_id))
    ).scalars()
    assert list(group_members) == []

    # The service account survives with its owner cleared and its org intact.
    refreshed_sa = (
        await session.execute(
            select(ServiceAccount).where(ServiceAccount.id == service_account_id)
        )
    ).scalar_one()
    assert refreshed_sa.owner_user_id is None
    assert refreshed_sa.organization_id == org.id

    # MCP tokens keep their rows for attribution and are revoked instead.
    refreshed_pat = (
        await session.execute(
            select(MCPPersonalAccessToken).where(MCPPersonalAccessToken.id == pat_id)
        )
    ).scalar_one()
    assert refreshed_pat.revoked_at is not None
    assert refreshed_pat.revoked_by == actor_id
    refreshed_rt = (
        await session.execute(
            select(MCPRefreshToken).where(MCPRefreshToken.id == refresh_token_id)
        )
    ).scalar_one()
    assert refreshed_rt.status == "revoked"


async def test_mirror_helper_sets_and_restores_rls_bypass(
    session: AsyncSession, org: Organization, workspace: Workspace, user: User
) -> None:
    """The legacy mirror runs under the bypass and hands the context back."""
    await ensure_member(session, org.id, user.id)
    await set_rls_context(session, org_id=org.id, workspace_id=None, user_id=user.id)

    async def read_bypass() -> str | None:
        return await session.scalar(
            text(f"SELECT current_setting('{RLS_VAR_BYPASS}', true)")
        )

    assert await read_bypass() == RLS_BYPASS_OFF

    seen: list[str | None] = []
    original_execute = session.execute

    async def spy(statement, *args, **kwargs):  # noqa: ANN001, ANN202
        if isinstance(statement, Insert):
            seen.append(await read_bypass())
        return await original_execute(statement, *args, **kwargs)

    with patch.object(session, "execute", spy):
        await mirror_workspace_membership(
            session, user_id=user.id, workspace_id=workspace.id
        )

    assert seen == [RLS_BYPASS_ON]
    assert await read_bypass() == RLS_BYPASS_OFF


def test_membership_deletion_has_one_choke_point() -> None:
    """Only ``OrgService.delete_member`` may delete a membership row.

    Removal revokes sessions, drops the legacy mirror and cascades role paths.
    A second deletion site would skip that and silently strand access.
    """
    repo_root = Path(__file__).resolve().parents[2]
    roots = (repo_root / "tracecat", repo_root / "packages")
    allowed = repo_root / "tracecat" / "organization" / "service.py"

    offenders = [
        path.relative_to(repo_root)
        for root in roots
        for path in root.rglob("*.py")
        if path != allowed and "delete(OrganizationMembership)" in path.read_text()
    ]

    assert offenders == [], (
        "Delete membership rows through OrgService.delete_member, not directly: "
        f"{offenders}"
    )
