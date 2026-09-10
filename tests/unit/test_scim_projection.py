"""Unit tests for the SCIM external-group projection."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from typing import cast as type_cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.scim.service import SCIMService

from tests.support.membership import (
    grant_org_membership,
    grant_org_membership_via_group,
    seed_external_group,
    seed_external_group_members,
    seed_external_user,
    seed_group_member,
)
from tracecat.auth.api_keys import generate_managed_api_key
from tracecat.auth.types import Role
from tracecat.authz.enums import GroupMemberSource
from tracecat.db.models import (
    AccessToken,
    ExternalGroupMapping,
    ExternalGroupMember,
    Group,
    GroupMember,
    GroupRoleAssignment,
    MCPPersonalAccessToken,
    MCPRefreshToken,
    Organization,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.mcp.personal_access_tokens.constants import MCP_PAT_PREFIX


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="SCIM Org", slug=f"scim-org-{org_id.hex[:8]}")
    session.add(org)
    await session.flush()
    return org


@pytest.fixture
def role(org: Organization) -> Role:
    return Role(
        type="service",
        organization_id=org.id,
        workspace_id=None,
        user_id=None,
        service_id="tracecat-service",
        scopes=frozenset({"*"}),
    )


@pytest.fixture
def service(session: AsyncSession, role: Role) -> SCIMService:
    return SCIMService(session, role)


async def _make_user(session: AsyncSession, org: Organization) -> User:
    # SCIM-linked in this org: the org guard refuses these, so this is what
    # makes the deprovisioning path's allow_scim_managed bypass load-bearing.
    user = User(
        id=uuid.uuid4(),
        email=f"scim-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()
    await seed_external_user(session, organization_id=org.id, user_id=user.id)
    await grant_org_membership_via_group(
        session, user_id=user.id, organization_id=org.id
    )
    return user


async def _make_group(session: AsyncSession, org: Organization) -> Group:
    group = Group(
        id=uuid.uuid4(),
        name=f"scim-group-{uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    session.add(group)
    await session.flush()
    return group


async def _members(
    session: AsyncSession, group_id: uuid.UUID
) -> dict[uuid.UUID, GroupMemberSource]:
    """Every group_member row for a group, keyed by user id."""
    rows = (
        (
            await session.execute(
                select(GroupMember.user_id, GroupMember.source).where(
                    GroupMember.group_id == group_id
                )
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


@pytest.mark.anyio
async def test_mapping_projects_external_members_into_group(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """A mapping projects the external group's members as scim-sourced rows."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-engineering"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id]
    )

    await service.create_mapping(external_group_id=external.id, group_id=group.id)

    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}


@pytest.mark.anyio
async def test_projection_never_touches_group_role_assignments(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Invariant 1: the projection creates and deletes no role assignments."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-invariant-1"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id]
    )

    async def assignment_ids() -> set[uuid.UUID]:
        return set(
            (
                await session.execute(
                    select(GroupRoleAssignment.id).where(
                        GroupRoleAssignment.organization_id == org.id
                    )
                )
            ).scalars()
        )

    before = await assignment_ids()
    mapping = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )
    assert await assignment_ids() == before

    await service.delete_mapping(mapping.id)
    assert await assignment_ids() == before


@pytest.mark.anyio
async def test_overlapping_mappings_do_not_evict_shared_member(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Invariant 2: another mapping still supplying the user blocks eviction."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    first = await seed_external_group(
        session, organization_id=org.id, external_id="idp-first"
    )
    second = await seed_external_group(
        session, organization_id=org.id, external_id="idp-second"
    )
    for external_group_id in (first.id, second.id):
        await seed_external_group_members(
            session, external_group_id=external_group_id, user_ids=[user.id]
        )

    first_mapping = await service.create_mapping(
        external_group_id=first.id, group_id=group.id
    )
    await service.create_mapping(external_group_id=second.id, group_id=group.id)
    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}

    # Dropping one supplier leaves the other one supplying the same user.
    await service.replace_external_group_members(first.id, [])
    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}

    await service.delete_mapping(first_mapping.id)
    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}

    # Only once the last supplier is gone is the row withdrawn.
    await service.replace_external_group_members(second.id, [])
    assert await _members(session, group.id) == {}


@pytest.mark.anyio
async def test_manual_membership_survives_projection(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Invariant 3: manual rows win, are not duplicated, and are never evicted."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    await seed_group_member(
        session, group_id=group.id, user_id=user.id, source=GroupMemberSource.MANUAL
    )
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-manual-overlap"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id]
    )

    mapping = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )
    # The pk is (user_id, group_id), so the manual row stands unchanged.
    assert await _members(session, group.id) == {user.id: GroupMemberSource.MANUAL}

    await service.delete_mapping(mapping.id)
    assert await _members(session, group.id) == {user.id: GroupMemberSource.MANUAL}


@pytest.mark.anyio
async def test_recompute_is_idempotent(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Invariant 4: a second recompute changes nothing."""
    users = [await _make_user(session, org) for _ in range(3)]
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-idempotent"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[u.id for u in users]
    )
    await service.create_mapping(external_group_id=external.id, group_id=group.id)

    first = await _members(session, group.id)
    assert first == {u.id: GroupMemberSource.SCIM for u in users}

    await service.recompute_group(group.id)
    assert await _members(session, group.id) == first
    await service.recompute_group(group.id)
    assert await _members(session, group.id) == first


@pytest.mark.anyio
async def test_replace_members_reconciles_as_a_set(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Replacing the provider's list adds and removes in one reconciliation."""
    kept = await _make_user(session, org)
    dropped = await _make_user(session, org)
    added = await _make_user(session, org)
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-replace"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[kept.id, dropped.id]
    )
    await service.create_mapping(external_group_id=external.id, group_id=group.id)
    assert set(await _members(session, group.id)) == {kept.id, dropped.id}

    await service.replace_external_group_members(external.id, [kept.id, added.id])
    assert set(await _members(session, group.id)) == {kept.id, added.id}


@pytest.mark.anyio
async def test_projection_grants_membership_through_group_role(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """A projected member inherits the group's org-wide role assignment."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    granted_role = await _org_role(session, org)
    session.add(
        GroupRoleAssignment(
            organization_id=org.id,
            group_id=group.id,
            workspace_id=None,
            role_id=granted_role,
        )
    )
    await session.flush()

    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-scoped"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id]
    )
    await service.create_mapping(external_group_id=external.id, group_id=group.id)

    # The scopes reach the user through the group's existing assignment; the
    # projection adds no user_role_assignment of its own.
    direct = (
        await session.execute(
            select(UserRoleAssignment.id).where(
                UserRoleAssignment.organization_id == org.id,
                UserRoleAssignment.user_id == user.id,
            )
        )
    ).scalars()
    assert not list(direct)
    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}


@pytest.mark.anyio
async def test_deleting_external_group_withdraws_its_members(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Deleting the synced group drops what it supplied and its mappings."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-deleted"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id]
    )
    await service.create_mapping(external_group_id=external.id, group_id=group.id)
    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}

    await service.delete_external_group(external.id)

    assert await _members(session, group.id) == {}
    remaining = (
        await session.execute(
            select(ExternalGroupMapping.id).where(
                ExternalGroupMapping.organization_id == org.id
            )
        )
    ).scalars()
    assert not list(remaining)


async def _org_role(session: AsyncSession, org: Organization) -> uuid.UUID:
    """Create a scopeless org role usable for a group assignment."""
    role = DBRole(
        name=f"scim-role-{uuid.uuid4().hex[:8]}",
        slug=None,
        description=None,
        organization_id=org.id,
    )
    session.add(role)
    await session.flush()
    return role.id


# =============================================================================
# Deprovisioning
# =============================================================================


async def _access_token_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(type_cast(Any, AccessToken.user_id) == user_id)
    return (await session.execute(stmt.select_from(AccessToken))).scalar_one()


async def _store_access_token(session: AsyncSession, user: User) -> None:
    session.add(AccessToken(token=f"token-{uuid.uuid4().hex}", user_id=user.id))
    await session.flush()


async def _store_mcp_token(
    session: AsyncSession, user: User, org: Organization
) -> MCPPersonalAccessToken:
    generated = generate_managed_api_key(prefix=MCP_PAT_PREFIX)
    token = MCPPersonalAccessToken(
        id=uuid.uuid4(),
        user_id=user.id,
        organization_id=org.id,
        workspace_id=None,
        name="Claude Desktop",
        key_id=generated.key_id,
        hashed=generated.hashed,
        salt=generated.salt_b64,
        preview=generated.preview(),
        created_by=user.id,
    )
    session.add(token)
    await session.flush()
    return token


async def _store_refresh_token(
    session: AsyncSession, user: User, org: Organization
) -> MCPRefreshToken:
    token = MCPRefreshToken(
        id=uuid.uuid4(),
        organization_id=org.id,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        family_id=uuid.uuid4(),
        user_id=user.id,
        client_id="claude-desktop",
        encrypted_metadata=b"encrypted",
        status="active",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(token)
    await session.flush()
    return token


async def _is_active(session: AsyncSession, user_id: uuid.UUID) -> bool:
    user = await session.get(User, user_id)
    assert user is not None
    return bool(user.is_active)


@pytest.mark.anyio
async def test_deprovision_revokes_credentials_and_withdraws_membership(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Deprovisioning clears sessions and tokens and drops both row sources."""
    user = await _make_user(session, org)
    other = await _make_user(session, org)
    scim_group = await _make_group(session, org)
    manual_group = await _make_group(session, org)

    await _store_access_token(session, user)
    await _store_access_token(session, other)
    mcp_token = await _store_mcp_token(session, user, org)
    other_token = await _store_mcp_token(session, other, org)
    refresh_token = await _store_refresh_token(session, user, org)
    other_refresh = await _store_refresh_token(session, other, org)

    await seed_group_member(
        session,
        group_id=manual_group.id,
        user_id=user.id,
        source=GroupMemberSource.MANUAL,
    )
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-deprovision"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id, other.id]
    )
    await service.create_mapping(external_group_id=external.id, group_id=scim_group.id)
    assert await _members(session, scim_group.id) == {
        user.id: GroupMemberSource.SCIM,
        other.id: GroupMemberSource.SCIM,
    }

    await service.deprovision_user(user.id)

    # The global account flag is never written: this is org-scoped removal.
    assert await _is_active(session, user.id) is True
    assert await _access_token_count(session, user.id) == 0
    await session.refresh(mcp_token)
    assert mcp_token.revoked_at is not None
    await session.refresh(refresh_token)
    assert refresh_token.status == "revoked"
    assert await _members(session, manual_group.id) == {}
    assert await _members(session, scim_group.id) == {other.id: GroupMemberSource.SCIM}

    # An untouched user keeps their session, token, and projected membership.
    assert await _access_token_count(session, other.id) == 1
    await session.refresh(other_token)
    assert other_token.revoked_at is None
    await session.refresh(other_refresh)
    assert other_refresh.status == "active"


@pytest.mark.anyio
async def test_deprovision_drops_the_projection_because_the_assignment_is_gone(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """The user leaves the projection via row removal, not an account flag."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-projection-drop"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id]
    )
    await service.create_mapping(external_group_id=external.id, group_id=group.id)
    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}

    await service.deprovision_user(user.id)

    assert await _members(session, group.id) == {}
    assert await _is_active(session, user.id) is True
    # The org-wide role assignment the removal deleted does not come back.
    assignments = (
        await session.execute(
            select(UserRoleAssignment.id).where(
                UserRoleAssignment.organization_id == org.id,
                UserRoleAssignment.user_id == user.id,
            )
        )
    ).scalars()
    assert not list(assignments)

    # A later recompute must not resurrect the row from stale shadow state.
    await service.recompute_group(group.id)
    assert await _members(session, group.id) == {}


@pytest.mark.anyio
async def test_deprovision_refuses_superuser(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """A customer's IdP must not be able to remove a platform admin."""
    superuser = await _make_user(session, org)
    superuser.is_superuser = True
    await session.flush()

    with pytest.raises(TracecatAuthorizationError):
        await service.deprovision_user(superuser.id)


@pytest.mark.anyio
async def test_deprovision_is_idempotent(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """A second deprovision is safe: the user is simply no longer a member."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    await seed_group_member(
        session, group_id=group.id, user_id=user.id, source=GroupMemberSource.MANUAL
    )
    await _store_access_token(session, user)
    token = await _store_mcp_token(session, user, org)

    await service.deprovision_user(user.id)
    await session.refresh(token)
    first_revoked_at = token.revoked_at
    assert first_revoked_at is not None
    assert await _members(session, group.id) == {}

    with pytest.raises(NoResultFound):
        await service.deprovision_user(user.id)

    assert await _access_token_count(session, user.id) == 0
    assert await _members(session, group.id) == {}
    await session.refresh(token)
    assert token.revoked_at == first_revoked_at


@pytest.mark.anyio
async def test_deprovision_unknown_user_is_rejected(service: SCIMService) -> None:
    """Deprovisioning a non-member raises rather than silently passing."""
    with pytest.raises(NoResultFound):
        await service.deprovision_user(uuid.uuid4())


@pytest.mark.anyio
async def test_deprovision_is_org_scoped(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Defect C regression: another org's group rows and membership survive.

    A user in orgs A and B, deprovisioned from A, stays a member of B with B's
    group_member rows intact.
    """
    other_org_id = uuid.uuid4()
    other_org = Organization(
        id=other_org_id,
        name="Other Org",
        slug=f"other-org-{other_org_id.hex[:8]}",
    )
    session.add(other_org)
    await session.flush()

    user = await _make_user(session, org)
    await grant_org_membership(session, user_id=user.id, organization_id=other_org.id)

    # A group row in each org, both manual so neither depends on a projection.
    group_a = await _make_group(session, org)
    group_b = await _make_group(session, other_org)
    for group_id in (group_a.id, group_b.id):
        await seed_group_member(
            session,
            group_id=group_id,
            user_id=user.id,
            source=GroupMemberSource.MANUAL,
        )

    # A shadow list in the other org must survive too.
    external_b = await seed_external_group(
        session, organization_id=other_org.id, external_id="idp-other-org"
    )
    await seed_external_group_members(
        session, external_group_id=external_b.id, user_ids=[user.id]
    )

    await service.deprovision_user(user.id)

    assert await _members(session, group_a.id) == {}
    assert await _members(session, group_b.id) == {user.id: GroupMemberSource.MANUAL}

    # The other org's role assignment and shadow membership are untouched.
    assignments = (
        await session.execute(
            select(UserRoleAssignment.id).where(
                UserRoleAssignment.organization_id == other_org.id,
                UserRoleAssignment.user_id == user.id,
            )
        )
    ).scalars()
    assert list(assignments)
    shadow = (
        await session.execute(
            select(ExternalGroupMember.user_id).where(
                ExternalGroupMember.external_group_id == external_b.id,
                ExternalGroupMember.user_id == user.id,
            )
        )
    ).scalars()
    assert list(shadow)
    assert await _is_active(session, user.id) is True


@pytest.mark.anyio
async def test_replace_members_locks_the_external_group(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Defect A: the replacement serializes on the external_group row.

    Two concurrent replacements from an empty list would otherwise each delete
    nothing and insert independently, leaving the union of both member lists.
    """
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-locking"
    )

    statements: list[str] = []
    original = session.execute

    async def record(statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        statements.append(str(statement))
        return await original(statement, *args, **kwargs)

    session.execute = record  # type: ignore[method-assign]
    try:
        await service.replace_external_group_members(external.id, [])
    finally:
        session.execute = original  # type: ignore[method-assign]

    locking = [s for s in statements if "FOR UPDATE" in s and "external_group" in s]
    assert locking, f"no locking select on external_group in {statements}"


@pytest.mark.anyio
async def test_delete_external_group_locks_it(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Deletion serializes on the external_group row.

    The mapping snapshot is taken before the delete, so a mapping created
    concurrently would supply members this deletion never reconciles.
    """
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-delete-locking"
    )

    statements: list[str] = []
    original = session.execute

    async def record(statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        statements.append(str(statement))
        return await original(statement, *args, **kwargs)

    session.execute = record  # type: ignore[method-assign]
    try:
        await service.delete_external_group(external.id)
    finally:
        session.execute = original  # type: ignore[method-assign]

    locking = [s for s in statements if "FOR UPDATE" in s and "external_group" in s]
    assert locking, f"no locking select on external_group in {statements}"


@pytest.mark.anyio
async def test_delete_mapping_locks_the_group_before_deleting(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """The group lock precedes the delete, matching create_mapping's order.

    create_mapping locks the group first; deleting in the reverse order would
    let the two operations wait on each other.
    """
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-mapping-lock"
    )
    group = await _make_group(session, org)
    mapping = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )

    # The mapping is removed through the ORM, so its DELETE is emitted by
    # flush(); the lock has to land before that flush, not before execute().
    events: list[str] = []
    original_execute = session.execute
    original_flush = session.flush

    async def record_execute(statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        rendered = str(statement)
        if "FOR UPDATE" in rendered and 'FROM "group"' in rendered:
            events.append("lock")
        return await original_execute(statement, *args, **kwargs)

    async def record_flush(*args, **kwargs):  # type: ignore[no-untyped-def]
        events.append("flush")
        return await original_flush(*args, **kwargs)

    session.execute = record_execute  # type: ignore[method-assign]
    session.flush = record_flush  # type: ignore[method-assign]
    try:
        await service.delete_mapping(mapping.id)
    finally:
        session.execute = original_execute  # type: ignore[method-assign]
        session.flush = original_flush  # type: ignore[method-assign]

    assert "lock" in events, f"no group lock issued: {events}"
    assert "flush" in events, f"the mapping delete never flushed: {events}"
    assert events.index("lock") < events.index("flush"), (
        f"the group must be locked before the mapping delete flushes: {events}"
    )


async def _make_scim_only_user(session: AsyncSession, org: Organization) -> User:
    """A user whose only org presence is the mapped group's assignment."""
    user = User(
        id=uuid.uuid4(),
        email=f"scim-only-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()
    await seed_external_user(session, organization_id=org.id, user_id=user.id)
    return user


@pytest.mark.anyio
async def test_deprovision_revokes_credentials_for_scim_only_user(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Defect 1: org presence supplied solely by a mapped group must still revoke.

    The projection strips the user's only org-presence path, so resolving the
    member after that work would leave credentials live.
    """
    user = await _make_scim_only_user(session, org)
    group = await _make_group(session, org)
    session.add(
        GroupRoleAssignment(
            organization_id=org.id,
            group_id=group.id,
            workspace_id=None,
            role_id=await _org_role(session, org),
        )
    )
    await session.flush()

    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-scim-only"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, user_ids=[user.id]
    )
    await service.create_mapping(external_group_id=external.id, group_id=group.id)

    # The mapped group is the user's only route into the organization.
    assert await _members(session, group.id) == {user.id: GroupMemberSource.SCIM}
    assert (
        await session.scalar(
            select(OrganizationMembership.user_id).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == org.id,
            )
        )
        is not None
    )

    await _store_access_token(session, user)
    mcp_token = await _store_mcp_token(session, user, org)
    refresh_token = await _store_refresh_token(session, user, org)

    await service.deprovision_user(user.id)

    assert await _access_token_count(session, user.id) == 0
    await session.refresh(mcp_token)
    assert mcp_token.revoked_at is not None
    await session.refresh(refresh_token)
    assert refresh_token.status == "revoked"
    assert await _members(session, group.id) == {}
    assert (
        await session.scalar(
            select(OrganizationMembership.user_id).where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.organization_id == org.id,
            )
        )
        is None
    )
