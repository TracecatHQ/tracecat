"""Unit tests for the SCIM group mapping administration service."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.scim.schemas import ExternalGroupMappingCreate
from tracecat_ee.scim.service import SCIMService

from tests.support.membership import (
    grant_org_membership_via_group,
    seed_external_group,
    seed_external_group_members,
    seed_external_user,
    seed_group_member,
)
from tracecat.auth.types import Role
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.db.models import (
    ExternalGroupMapping,
    ExternalGroupMember,
    ExternalUser,
    Group,
    GroupMember,
    Organization,
    OrganizationMembership,
    ScimConnection,
    User,
    effective_group_members,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)


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
async def other_org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Other Org", slug=f"other-org-{org_id.hex[:8]}")
    session.add(org)
    await session.flush()
    return org


def _role(org: Organization, *scopes: str) -> Role:
    return Role(
        type="service",
        organization_id=org.id,
        workspace_id=None,
        user_id=None,
        service_id="tracecat-service",
        scopes=frozenset(scopes),
    )


@pytest.fixture
def admin_role(org: Organization) -> Role:
    return _role(
        org,
        "org:rbac:read",
        "org:rbac:create",
        "org:rbac:delete",
    )


@pytest.fixture
def service(session: AsyncSession, admin_role: Role) -> SCIMService:
    return SCIMService(session, admin_role)


async def _make_user(session: AsyncSession, org: Organization) -> User:
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
    if not await session.scalar(
        select(ScimConnection.id).where(ScimConnection.organization_id == org.id)
    ):
        session.add(
            ScimConnection(
                id=uuid.uuid4(),
                organization_id=org.id,
                key_id=uuid.uuid4().hex[:16],
                hashed="x",
                salt="y",
                preview="scim_...",
                status=ScimConnectionStatus.ACTIVE,
            )
        )
    group = Group(
        id=uuid.uuid4(),
        name=f"scim-group-{uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    session.add(group)
    await session.flush()
    return group


async def _idp_members(session: AsyncSession, group_id: uuid.UUID) -> set[uuid.UUID]:
    """Users the group grants access to through a mapping, read live."""
    stmt = (
        select(ExternalUser.user_id)
        .join(
            ExternalGroupMember,
            ExternalGroupMember.external_user_id == ExternalUser.id,
        )
        .join(
            ExternalGroupMapping,
            ExternalGroupMapping.external_group_id
            == ExternalGroupMember.external_group_id,
        )
        .where(ExternalGroupMapping.group_id == group_id, ExternalUser.active)
    )
    return set((await session.execute(stmt)).scalars())


async def _manual_members(session: AsyncSession, group_id: uuid.UUID) -> set[uuid.UUID]:
    """Users held by a stored group_member row."""
    stmt = select(GroupMember.user_id).where(GroupMember.group_id == group_id)
    return set((await session.execute(stmt)).scalars())


# =============================================================================
# Listing external groups
# =============================================================================


@pytest.mark.anyio
async def test_list_external_groups_is_empty_without_a_sync(
    service: SCIMService,
) -> None:
    """An organization the provider has never pushed to has nothing to offer."""
    assert await service.list_external_groups() == []


@pytest.mark.anyio
async def test_list_external_groups_counts_members(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Each synced group carries its own member count, including zero."""
    populated = await seed_external_group(
        session, organization_id=org.id, external_id="idp-eng", display_name="Eng"
    )
    await seed_external_group(
        session, organization_id=org.id, external_id="idp-ops", display_name="Ops"
    )
    users = [await _make_user(session, org) for _ in range(2)]
    external_user_ids = [
        await seed_external_user(session, organization_id=org.id, user_id=u.id)
        for u in users
    ]
    await seed_external_group_members(
        session, external_group_id=populated.id, external_user_ids=external_user_ids
    )

    listed = await service.list_external_groups()

    assert [(g.display_name, g.member_count) for g in listed] == [
        ("Eng", 2),
        ("Ops", 0),
    ]
    assert {g.external_id for g in listed} == {"idp-eng", "idp-ops"}


@pytest.mark.anyio
async def test_list_external_groups_excludes_other_organizations(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    service: SCIMService,
) -> None:
    """Another tenant's synced groups are never offered as mapping sources."""
    await seed_external_group(
        session, organization_id=org.id, external_id="idp-mine", display_name="Mine"
    )
    await seed_external_group(
        session,
        organization_id=other_org.id,
        external_id="idp-theirs",
        display_name="Theirs",
    )

    listed = await service.list_external_groups()

    assert [g.external_id for g in listed] == ["idp-mine"]


# =============================================================================
# Listing mappings
# =============================================================================


@pytest.mark.anyio
async def test_list_mappings_joins_both_sides(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """A listed mapping carries the display detail a UI needs to render it."""
    external = await seed_external_group(
        session,
        organization_id=org.id,
        external_id="idp-eng",
        display_name="Engineering",
    )
    group = await _make_group(session, org)
    created = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )

    listed = await service.list_mappings()

    assert len(listed) == 1
    row = listed[0]
    assert row.id == created.id
    assert row.external_group_id == external.id
    assert row.external_group_external_id == "idp-eng"
    assert row.external_group_display_name == "Engineering"
    assert row.group_id == group.id
    assert row.group_name == group.name


@pytest.mark.anyio
async def test_list_mappings_excludes_other_organizations(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    service: SCIMService,
) -> None:
    """A mapping in another tenant never appears in this organization's list."""
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-mine"
    )
    group = await _make_group(session, org)
    await service.create_mapping(external_group_id=external.id, group_id=group.id)

    their_external = await seed_external_group(
        session, organization_id=other_org.id, external_id="idp-theirs"
    )
    their_group = await _make_group(session, other_org)
    await SCIMService(
        session,
        _role(other_org, "org:rbac:read", "org:rbac:create"),
    ).create_mapping(external_group_id=their_external.id, group_id=their_group.id)

    listed = await service.list_mappings()

    assert [row.external_group_external_id for row in listed] == ["idp-mine"]


# =============================================================================
# Creating and deleting through the admin surface
# =============================================================================


@pytest.mark.anyio
async def test_create_mapping_projects_members_immediately(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Creating a mapping grants membership in the same call."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-eng"
    )
    external_user_id = await seed_external_user(
        session, organization_id=org.id, user_id=user.id
    )
    await seed_external_group_members(
        session, external_group_id=external.id, external_user_ids=[external_user_id]
    )

    await service.create_mapping(external_group_id=external.id, group_id=group.id)

    assert await _idp_members(session, group.id) == {user.id}


@pytest.mark.anyio
async def test_delete_last_mapping_freezes_members_as_manual(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Unmapping the last source keeps the access as editable manual rows."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-eng"
    )
    external_user_id = await seed_external_user(
        session, organization_id=org.id, user_id=user.id
    )
    await seed_external_group_members(
        session, external_group_id=external.id, external_user_ids=[external_user_id]
    )
    mapping = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )

    await service.delete_mapping(mapping.id)

    assert await _idp_members(session, group.id) == set()
    # Access survives the unmapping, now as rows an admin can edit.
    assert await _manual_members(session, group.id) == {user.id}


@pytest.mark.anyio
async def test_delete_mapping_keeps_membership_another_mapping_supplies(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """A second mapping still supplying the member keeps the row in place."""
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    first = await seed_external_group(
        session, organization_id=org.id, external_id="idp-first"
    )
    second = await seed_external_group(
        session, organization_id=org.id, external_id="idp-second"
    )
    external_user_id = await seed_external_user(
        session, organization_id=org.id, user_id=user.id
    )
    for external in (first, second):
        await seed_external_group_members(
            session,
            external_group_id=external.id,
            external_user_ids=[external_user_id],
        )
    mapping = await service.create_mapping(
        external_group_id=first.id, group_id=group.id
    )
    await service.create_mapping(external_group_id=second.id, group_id=group.id)

    await service.delete_mapping(mapping.id)

    assert await _idp_members(session, group.id) == {user.id}


@pytest.mark.anyio
async def test_creating_a_duplicate_mapping_returns_the_existing_row(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """The unique pair makes a repeat create idempotent rather than an error."""
    group = await _make_group(session, org)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-eng"
    )

    first = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )
    second = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )

    assert first.id == second.id
    assert len(await service.list_mappings()) == 1


# =============================================================================
# Cross-organization isolation
# =============================================================================


@pytest.mark.anyio
async def test_mapping_another_organizations_external_group_is_rejected(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    service: SCIMService,
) -> None:
    """The source must belong to the caller's organization."""
    theirs = await seed_external_group(
        session, organization_id=other_org.id, external_id="idp-theirs"
    )
    group = await _make_group(session, org)

    with pytest.raises(TracecatNotFoundError):
        await service.create_mapping(external_group_id=theirs.id, group_id=group.id)


@pytest.mark.anyio
async def test_mapping_into_another_organizations_group_is_rejected(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    service: SCIMService,
) -> None:
    """The target group must belong to the caller's organization."""
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-mine"
    )
    their_group = await _make_group(session, other_org)

    with pytest.raises(TracecatNotFoundError):
        await service.create_mapping(
            external_group_id=external.id, group_id=their_group.id
        )


@pytest.mark.anyio
async def test_deleting_another_organizations_mapping_is_rejected(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    service: SCIMService,
) -> None:
    """A mapping id from another tenant is not found, not deleted."""
    their_external = await seed_external_group(
        session, organization_id=other_org.id, external_id="idp-theirs"
    )
    their_group = await _make_group(session, other_org)
    theirs = await SCIMService(
        session,
        _role(other_org, "org:rbac:read", "org:rbac:create"),
    ).create_mapping(external_group_id=their_external.id, group_id=their_group.id)

    with pytest.raises(TracecatNotFoundError):
        await service.delete_mapping(theirs.id)


@pytest.mark.anyio
async def test_reading_another_organizations_mapping_is_rejected(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    service: SCIMService,
) -> None:
    """A single read is org-scoped the same way the list is."""
    their_external = await seed_external_group(
        session, organization_id=other_org.id, external_id="idp-theirs"
    )
    their_group = await _make_group(session, other_org)
    theirs = await SCIMService(
        session,
        _role(other_org, "org:rbac:read", "org:rbac:create"),
    ).create_mapping(external_group_id=their_external.id, group_id=their_group.id)

    with pytest.raises(TracecatNotFoundError):
        await service.get_mapping(theirs.id)


# =============================================================================
# Scopes
# =============================================================================


@pytest.mark.anyio
async def test_listing_requires_the_read_scope(
    session: AsyncSession, org: Organization
) -> None:
    """Reads are refused without org:rbac:read."""
    service = SCIMService(session, _role(org, "org:rbac:create"))

    with pytest.raises(TracecatAuthorizationError):
        await service.list_external_groups()
    with pytest.raises(TracecatAuthorizationError):
        await service.list_mappings()


@pytest.mark.anyio
async def test_creating_requires_the_create_scope(
    session: AsyncSession, org: Organization
) -> None:
    """A mapping is refused without org:rbac:create."""
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-eng"
    )
    group = await _make_group(session, org)
    service = SCIMService(session, _role(org, "org:rbac:read"))

    with pytest.raises(TracecatAuthorizationError):
        await service.create_mapping(external_group_id=external.id, group_id=group.id)


@pytest.mark.anyio
async def test_deleting_requires_the_delete_scope(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """Removing a mapping is refused without org:rbac:delete."""
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-eng"
    )
    group = await _make_group(session, org)
    mapping = await service.create_mapping(
        external_group_id=external.id, group_id=group.id
    )
    without_delete = SCIMService(session, _role(org, "org:rbac:read"))

    with pytest.raises(TracecatAuthorizationError):
        await without_delete.delete_mapping(mapping.id)


# =============================================================================
# Activation
# =============================================================================


@pytest.mark.anyio
async def test_activation_admits_pushed_users_and_installs_mappings(
    session: AsyncSession, org: Organization
) -> None:
    """Activation admits the collected directory and applies the mappings."""
    connection = ScimConnection(
        id=uuid.uuid4(),
        organization_id=org.id,
        key_id=uuid.uuid4().hex[:16],
        hashed="x",
        salt="y",
        preview="scim_...",
        status=ScimConnectionStatus.PENDING,
    )
    session.add(connection)
    user = User(
        id=uuid.uuid4(),
        email=f"pending-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()
    external_user_id = await seed_external_user(
        session, organization_id=org.id, user_id=user.id
    )
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-activate"
    )
    await seed_external_group_members(
        session, external_group_id=external.id, external_user_ids=[external_user_id]
    )
    group = await _make_group(session, org)
    # Nothing is admitted while the connection is pending.
    assert not await _is_member(session, user_id=user.id, organization_id=org.id)

    role = _role(org, "org:rbac:read", "org:rbac:create", "org:rbac:update")
    await SCIMService(session, role).activate(
        [ExternalGroupMappingCreate(external_group_id=external.id, group_id=group.id)]
    )

    assert await _is_member(session, user_id=user.id, organization_id=org.id)
    assert await _idp_members(session, group.id) == {user.id}
    await session.refresh(connection)
    assert connection.status == ScimConnectionStatus.ACTIVE


@pytest.mark.anyio
async def test_activation_review_reports_the_plan_without_storing_it(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    """The review names the manual rows a mapping would purge, and changes nothing."""
    member = await _make_user(session, org)
    group = await _make_group(session, org)
    await seed_group_member(session, group_id=group.id, user_id=member.id)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="idp-review"
    )

    review = await service.review_activation(
        [ExternalGroupMappingCreate(external_group_id=external.id, group_id=group.id)]
    )

    assert [p.manual_members_purged for p in review.plans] == [[member.id]]
    assert [p.users_losing_access for p in review.plans] == [[member.id]]
    # A review is a read: the manual row and the absent mapping both survive.
    assert await _manual_members(session, group.id) == {member.id}
    assert await service.list_mappings() == []


async def _is_member(
    session: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
) -> bool:
    stmt = select(OrganizationMembership).where(
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.organization_id == organization_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


@pytest.mark.anyio
async def test_pending_mapping_preserves_manual_members(
    session: AsyncSession, org: Organization, service: SCIMService
) -> None:
    group = await _make_group(session, org)
    users = [await _make_user(session, org) for _ in range(2)]
    for user in users:
        await seed_group_member(session, group_id=group.id, user_id=user.id)
    external = await seed_external_group(
        session, organization_id=org.id, external_id="pending-source"
    )
    connection = (
        await session.execute(
            select(ScimConnection).where(ScimConnection.organization_id == org.id)
        )
    ).scalar_one()
    connection.status = ScimConnectionStatus.PENDING
    await session.flush()
    with pytest.raises(TracecatConflictError):
        await service.create_mapping(external_group_id=external.id, group_id=group.id)
    assert await _manual_members(session, group.id) == {user.id for user in users}
    assert await service.list_mappings() == []


@pytest.mark.anyio
@pytest.mark.parametrize("other_source", [False, True])
async def test_provider_group_deletion_drops_only_its_membership(
    session: AsyncSession, org: Organization, service: SCIMService, other_source: bool
) -> None:
    user = await _make_user(session, org)
    group = await _make_group(session, org)
    external_user_id = await seed_external_user(
        session, organization_id=org.id, user_id=user.id
    )
    source = await seed_external_group(
        session, organization_id=org.id, external_id="deleted-source"
    )
    await seed_external_group_members(
        session, external_group_id=source.id, external_user_ids=[external_user_id]
    )
    await service.create_mapping(external_group_id=source.id, group_id=group.id)
    if other_source:
        peer = await seed_external_group(
            session, organization_id=org.id, external_id="surviving-source"
        )
        await seed_external_group_members(
            session, external_group_id=peer.id, external_user_ids=[external_user_id]
        )
        await service.create_mapping(external_group_id=peer.id, group_id=group.id)
    members = select(effective_group_members.c.user_id).where(
        effective_group_members.c.group_id == group.id
    )
    assert set((await session.execute(members)).scalars()) == {user.id}
    await service.delete_external_group(source.id)
    assert set((await session.execute(members)).scalars()) == (
        {user.id} if other_source else set()
    )
    assert await _manual_members(session, group.id) == set()
    assert await _is_member(session, user_id=user.id, organization_id=org.id)
