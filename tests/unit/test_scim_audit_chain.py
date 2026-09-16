"""The SCIM audit chain: a provider-driven removal is attributed to the connection.

This is the test the ``scim`` role type exists for. A SCIM-authenticated
deprovision must emit ``organization_member``/``delete`` with an actor type of
``SCIM`` and the connection's id as the actor, never a user or service account.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.scim.connections import ScimConnectionService
from tracecat_ee.scim.credentials import authenticate_scim_connection
from tracecat_ee.scim.service import SCIMService

from tests.support.membership import grant_org_membership, seed_external_user
from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditEvent
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.models import Organization, User
from tracecat.tiers import defaults as tier_defaults


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


@pytest.fixture(autouse=True)
def enable_rbac_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"rbac_addons": True}),
    )


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="SCIM Org", slug=f"scim-org-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
def auth_session(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the auth bulkhead session at the test session."""

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        "tracecat_ee.scim.credentials.get_async_session_auth_context_manager",
        _session_cm,
    )


@pytest.fixture
async def admin_user(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
def admin_role(org: Organization, admin_user: User) -> Role:
    return Role(
        type="user",
        user_id=admin_user.id,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=ORG_ADMIN_SCOPES,
    )


@pytest.fixture
async def member(session: AsyncSession, org: Organization) -> User:
    """A member linked to this organization's identity provider."""
    user = User(
        id=uuid.uuid4(),
        email=f"member-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    await grant_org_membership(session, user_id=user.id, organization_id=org.id)
    await seed_external_user(session, organization_id=org.id, user_id=user.id)
    await session.commit()
    return user


@pytest.mark.anyio
async def test_scim_deprovision_is_audited_as_the_connection(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    member: User,
    monkeypatch: pytest.MonkeyPatch,
    auth_session: None,
) -> None:
    """A SCIM-driven removal is attributed to the connection, not a user."""
    issued = await ScimConnectionService(session, role=admin_role).issue_token()
    connection_id = issued.connection.id

    scim_role = await authenticate_scim_connection(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=issued.token)
    )
    assert scim_role.type == "scim"

    events: list[AuditEvent] = []

    async def capture(self: AuditService, **kwargs: object) -> None:
        # Exercise the real payload builder so actor derivation is under test.
        events.append(
            self._build_payload(  # pyright: ignore[reportPrivateUsage]
                resource_type=kwargs["resource_type"],  # pyright: ignore[reportArgumentType]
                action=kwargs["action"],  # pyright: ignore[reportArgumentType]
                resource_id=kwargs.get("resource_id"),  # pyright: ignore[reportArgumentType]
                status=kwargs["status"],  # pyright: ignore[reportArgumentType]
                actor_label=None,
                ip_address=None,
                user_agent=None,
                data=None,
            )
        )

    monkeypatch.setattr(AuditService, "create_event", capture)

    token = ctx_role.set(scim_role)
    try:
        await SCIMService(session, role=scim_role).deprovision_user(member.id)
    finally:
        ctx_role.reset(token)

    terminal = [e for e in events if e.status is AuditEventStatus.SUCCESS]
    assert terminal, f"no success event emitted; got {[e.status for e in events]}"
    event = terminal[-1]

    assert event.resource_type == "organization_member"
    assert event.action == "delete"
    assert event.actor_type is AuditEventActor.SCIM
    assert event.actor_id == connection_id
    assert event.organization_id == org.id
    assert event.resource_id == member.id
    # A connection is not a person: no email is attached to the event.
    assert event.actor_label is None


@pytest.mark.anyio
async def test_user_driven_removal_is_still_attributed_to_the_user(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    member: User,
    monkeypatch: pytest.MonkeyPatch,
    auth_session: None,
) -> None:
    """The SCIM branch must not change attribution for ordinary admin removals."""
    events: list[AuditEvent] = []

    async def capture(self: AuditService, **kwargs: object) -> None:
        events.append(
            self._build_payload(  # pyright: ignore[reportPrivateUsage]
                resource_type=kwargs["resource_type"],  # pyright: ignore[reportArgumentType]
                action=kwargs["action"],  # pyright: ignore[reportArgumentType]
                resource_id=kwargs.get("resource_id"),  # pyright: ignore[reportArgumentType]
                status=kwargs["status"],  # pyright: ignore[reportArgumentType]
                actor_label=None,
                ip_address=None,
                user_agent=None,
                data=None,
            )
        )

    monkeypatch.setattr(AuditService, "create_event", capture)

    from tracecat.organization.service import OrgService

    token = ctx_role.set(admin_role)
    try:
        await OrgService(session, role=admin_role).delete_member(
            member.id, allow_scim_managed=True
        )
    finally:
        ctx_role.reset(token)

    terminal = [e for e in events if e.status is AuditEventStatus.SUCCESS]
    assert terminal
    event = terminal[-1]
    assert event.actor_type is AuditEventActor.USER
    assert event.actor_id == admin_role.user_id


@pytest.mark.anyio
async def test_connection_issue_and_revoke_are_audited(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Minting and revoking the credential are themselves security events."""
    events: list[AuditEvent] = []

    async def capture(self: AuditService, **kwargs: object) -> None:
        events.append(
            self._build_payload(  # pyright: ignore[reportPrivateUsage]
                resource_type=kwargs["resource_type"],  # pyright: ignore[reportArgumentType]
                action=kwargs["action"],  # pyright: ignore[reportArgumentType]
                resource_id=kwargs.get("resource_id"),  # pyright: ignore[reportArgumentType]
                status=kwargs["status"],  # pyright: ignore[reportArgumentType]
                actor_label=None,
                ip_address=None,
                user_agent=None,
                data=None,
            )
        )

    monkeypatch.setattr(AuditService, "create_event", capture)
    service = ScimConnectionService(session, role=admin_role)

    token = ctx_role.set(admin_role)
    try:
        issued = await service.issue_token()
        await service.revoke()
    finally:
        ctx_role.reset(token)

    succeeded = [e for e in events if e.status is AuditEventStatus.SUCCESS]
    by_action = {e.action: e for e in succeeded if e.resource_type == "scim_connection"}

    assert "create" in by_action, f"issue emitted no event; got {by_action}"
    assert "revoke" in by_action, f"revoke emitted no event; got {by_action}"

    created = by_action["create"]
    assert created.actor_type is AuditEventActor.USER
    assert created.actor_id == admin_user.id
    assert created.organization_id == org.id
    assert created.resource_id == issued.connection.id


@pytest.mark.anyio
async def test_scim_writes_emit_their_audit_events(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provisioning, directory pushes and mapping changes are each recorded.

    Recompute stays silent: it is cache reconciliation, not a decision.
    """
    from tracecat_ee.scim.provisioning import ScimProvisioningService

    from tracecat.db.models import Group

    events: list[AuditEvent] = []

    async def capture(self: AuditService, **kwargs: object) -> None:
        events.append(
            self._build_payload(  # pyright: ignore[reportPrivateUsage]
                resource_type=kwargs["resource_type"],  # pyright: ignore[reportArgumentType]
                action=kwargs["action"],  # pyright: ignore[reportArgumentType]
                resource_id=kwargs.get("resource_id"),  # pyright: ignore[reportArgumentType]
                status=kwargs["status"],  # pyright: ignore[reportArgumentType]
                actor_label=None,
                ip_address=None,
                user_agent=None,
                data=None,
            )
        )

    monkeypatch.setattr(AuditService, "create_event", capture)

    group = Group(
        id=uuid.uuid4(), name=f"g-{uuid.uuid4().hex[:8]}", organization_id=org.id
    )
    session.add(group)
    await session.flush()

    token = ctx_role.set(admin_role)
    try:
        provisioned = await ScimProvisioningService(
            session, role=admin_role
        ).provision_user(
            external_id=f"idp-{uuid.uuid4().hex[:8]}",
            email=f"scim-{uuid.uuid4().hex[:8]}@tracecat.com",
        )
        scim = SCIMService(session, role=admin_role)
        external = await scim.upsert_external_group(
            external_id=f"eg-{uuid.uuid4().hex[:8]}", display_name="Engineering"
        )
        await scim.replace_external_group_members(external.id, [provisioned.user.id])
        mapping = await scim.create_mapping(
            external_group_id=external.id, group_id=group.id
        )
        await scim.delete_mapping(mapping.id)
    finally:
        ctx_role.reset(token)

    succeeded = {
        (e.resource_type, e.action)
        for e in events
        if e.status is AuditEventStatus.SUCCESS
    }

    assert ("scim_user", "create") in succeeded, succeeded
    assert ("scim_directory", "sync") in succeeded, succeeded
    assert ("scim_group_mapping", "create") in succeeded, succeeded
    assert ("scim_group_mapping", "delete") in succeeded, succeeded
