"""Tests for database-driven eviction of members with no role paths.

A member stays present while they hold any direct assignment or any group link
in the organization; losing the last one removes them and cascades or revokes
everything hanging off the row.

The triggers are DEFERRED, so they run at COMMIT. The shared ``session``
fixture keeps every test inside one savepoint that is rolled back, where a real
COMMIT never happens, so these tests own a committing session and clean up
their own rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tests.support.membership import (
    grant_org_membership,
    grant_org_membership_via_group,
    grant_workspace_membership,
)
from tracecat.db.models import (
    Group,
    GroupMember,
    MCPPersonalAccessToken,
    MCPRefreshToken,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
    Workspace,
)

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture
async def committing_session() -> AsyncGenerator[AsyncSession, None]:
    """A session whose commits reach the database, so deferred triggers fire."""
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def org(committing_session: AsyncSession) -> AsyncGenerator[Organization, None]:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Evict Org", slug=f"evict-org-{org_id.hex[:8]}")
    committing_session.add(org)
    await committing_session.commit()
    yield org
    # Workspace holds a RESTRICT reference, so it goes before the organization,
    # whose delete then cascades every other row these tests created.
    await committing_session.execute(
        delete(Workspace).where(Workspace.organization_id == org_id)
    )
    await committing_session.delete(org)
    await committing_session.commit()


@pytest.fixture
async def workspace(committing_session: AsyncSession, org: Organization) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(), name="Evict Workspace", organization_id=org.id
    )
    committing_session.add(workspace)
    await committing_session.commit()
    return workspace


@pytest.fixture
async def member(committing_session: AsyncSession) -> AsyncGenerator[User, None]:
    user = User(
        id=uuid.uuid4(),
        email=f"evict-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
    )
    committing_session.add(user)
    await committing_session.commit()
    yield user
    await committing_session.delete(user)
    await committing_session.commit()


async def _is_present(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> bool:
    return (
        await session.execute(
            select(OrganizationMembership.user_id).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none() is not None


async def test_last_assignment_removal_evicts_and_revokes(
    committing_session: AsyncSession, org: Organization, member: User
) -> None:
    """Losing the final direct assignment removes the row and its tokens."""
    await grant_org_membership(
        committing_session, user_id=member.id, organization_id=org.id
    )
    pat = MCPPersonalAccessToken(
        id=uuid.uuid4(),
        user_id=member.id,
        organization_id=org.id,
        name="pat",
        key_id=uuid.uuid4().hex[:16],
        hashed="hashed",
        salt="salt",
        preview="preview",
    )
    refresh = MCPRefreshToken(
        id=uuid.uuid4(),
        token_hash=uuid.uuid4().hex,
        family_id=uuid.uuid4(),
        user_id=member.id,
        organization_id=org.id,
        client_id="client",
        encrypted_metadata=b"\x00",
        status="active",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    committing_session.add_all([pat, refresh])
    await committing_session.commit()
    pat_id, refresh_id = pat.id, refresh.id

    await committing_session.execute(
        delete(UserRoleAssignment).where(
            UserRoleAssignment.user_id == member.id,
            UserRoleAssignment.organization_id == org.id,
        )
    )
    await committing_session.commit()
    committing_session.expunge_all()

    assert not await _is_present(committing_session, member.id, org.id)
    revoked_at = (
        await committing_session.execute(
            select(MCPPersonalAccessToken.revoked_at).where(
                MCPPersonalAccessToken.id == pat_id
            )
        )
    ).scalar_one()
    assert revoked_at is not None
    status = (
        await committing_session.execute(
            select(MCPRefreshToken.status).where(MCPRefreshToken.id == refresh_id)
        )
    ).scalar_one()
    assert status == "revoked"


async def test_last_group_link_removal_evicts(
    committing_session: AsyncSession, org: Organization, member: User
) -> None:
    """A group link is a role path, so losing the last one evicts."""
    group = await grant_org_membership_via_group(
        committing_session, user_id=member.id, organization_id=org.id
    )
    await committing_session.commit()

    await committing_session.execute(
        delete(GroupMember).where(
            GroupMember.user_id == member.id, GroupMember.group_id == group.id
        )
    )
    await committing_session.commit()
    committing_session.expunge_all()

    assert not await _is_present(committing_session, member.id, org.id)


async def test_assignment_removal_keeps_member_with_a_group_link(
    committing_session: AsyncSession, org: Organization, member: User
) -> None:
    """A remaining group link keeps the member present."""
    await grant_org_membership(
        committing_session, user_id=member.id, organization_id=org.id
    )
    await grant_org_membership_via_group(
        committing_session, user_id=member.id, organization_id=org.id
    )
    await committing_session.commit()

    await committing_session.execute(
        delete(UserRoleAssignment).where(
            UserRoleAssignment.user_id == member.id,
            UserRoleAssignment.organization_id == org.id,
        )
    )
    await committing_session.commit()
    committing_session.expunge_all()

    assert await _is_present(committing_session, member.id, org.id)


async def test_swapping_roles_in_one_transaction_keeps_the_member(
    committing_session: AsyncSession,
    org: Organization,
    member: User,
    workspace: Workspace,
) -> None:
    """The trigger is deferred, so a delete-then-insert never evicts."""
    await grant_workspace_membership(
        committing_session,
        user_id=member.id,
        organization_id=org.id,
        workspace_id=workspace.id,
    )
    await committing_session.commit()
    role_id = (
        await committing_session.execute(
            select(UserRoleAssignment.role_id).where(
                UserRoleAssignment.user_id == member.id
            )
        )
    ).scalar_one()

    await committing_session.execute(
        delete(UserRoleAssignment).where(UserRoleAssignment.user_id == member.id)
    )
    committing_session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=member.id,
            workspace_id=None,
            role_id=role_id,
        )
    )
    await committing_session.commit()
    committing_session.expunge_all()

    assert await _is_present(committing_session, member.id, org.id)


async def test_group_delete_cascade_evicts_group_only_members(
    committing_session: AsyncSession, org: Organization, member: User
) -> None:
    """Cascaded group_member deletes re-enter the trigger and still evict."""
    group = await grant_org_membership_via_group(
        committing_session, user_id=member.id, organization_id=org.id
    )
    await committing_session.commit()
    group_id = group.id

    await committing_session.execute(delete(Group).where(Group.id == group_id))
    await committing_session.commit()
    committing_session.expunge_all()

    assert not await _is_present(committing_session, member.id, org.id)


async def test_explicit_member_delete_still_works(
    committing_session: AsyncSession, org: Organization, member: User
) -> None:
    """Deleting the row directly cascades without a double-delete error."""
    await grant_org_membership(
        committing_session, user_id=member.id, organization_id=org.id
    )
    await committing_session.commit()

    await committing_session.execute(
        delete(OrganizationMembership).where(
            OrganizationMembership.user_id == member.id,
            OrganizationMembership.organization_id == org.id,
        )
    )
    await committing_session.commit()
    committing_session.expunge_all()

    assert not await _is_present(committing_session, member.id, org.id)


async def test_superuser_without_paths_keeps_membership(
    committing_session: AsyncSession, org: Organization
) -> None:
    """A superuser is undeletable, so losing every path keeps the row."""
    user = User(
        id=uuid.uuid4(),
        email=f"evict-super-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
        is_superuser=True,
    )
    committing_session.add(user)
    await committing_session.commit()
    try:
        await grant_org_membership(
            committing_session, user_id=user.id, organization_id=org.id
        )
        await committing_session.commit()

        await committing_session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.organization_id == org.id,
            )
        )
        await committing_session.commit()
        committing_session.expunge_all()

        assert await _is_present(committing_session, user.id, org.id)
    finally:
        await committing_session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id
            )
        )
        await committing_session.delete(user)
        await committing_session.commit()


async def test_triggers_are_deferred_constraint_triggers(
    committing_session: AsyncSession,
) -> None:
    """Deferral is what keeps a delete-then-insert save from evicting."""
    rows = (
        await committing_session.execute(
            text(
                "SELECT tgname, tginitdeferred FROM pg_trigger "
                "WHERE tgname LIKE '%drop_membership%' ORDER BY tgname"
            )
        )
    ).all()

    assert [(name, deferred) for name, deferred in rows] == [
        ("trg_group_member_drop_membership", True),
        ("trg_user_role_assignment_drop_membership", True),
    ]
