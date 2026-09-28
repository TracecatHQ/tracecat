"""Tests for the invitation email outbox poller."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from tracecat_ee.admin.organizations.service import AdminOrgService

from tests.database import TEST_DB_CONFIG
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import Organization, OrganizationInvitation, User
from tracecat.db.models import Role as DBRole
from tracecat.email.transport import EmailDeliveryError, OutboundEmail, SMTPTransport
from tracecat.exceptions import TracecatConflictError
from tracecat.invitations.consumer import (
    MAX_EMAIL_ATTEMPTS,
    deliver_next_invitation,
    run_invitation_email_tick,
)
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.service import RESEND_COOLDOWN
from tracecat.organization.service import OrgService


class FakeTransport:
    """Records sends, optionally failing with a chosen error."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[OutboundEmail] = []

    async def send(self, message: OutboundEmail) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(message)


@pytest.fixture(autouse=True)
def tick_session(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the tick's own session through the test transaction."""

    @contextlib.asynccontextmanager
    async def fake_session_context() -> AsyncGenerator[AsyncSession, None]:
        yield session

    monkeypatch.setattr(
        "tracecat.invitations.consumer.get_async_session_bypass_rls_context_manager",
        fake_session_context,
    )


def _patch_transport(monkeypatch: pytest.MonkeyPatch, transport: FakeTransport) -> None:
    """Keep `from_config`'s None-when-unconfigured contract, swap the sender."""
    real_from_config = SMTPTransport.from_config

    def fake_from_config() -> FakeTransport | None:
        return transport if real_from_config() is not None else None

    monkeypatch.setattr(
        "tracecat.invitations.consumer.SMTPTransport.from_config",
        staticmethod(fake_from_config),
    )


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Outbox Org",
        slug=f"outbox-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def org_role(session: AsyncSession, org: Organization) -> DBRole:
    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug=f"organization-member-{uuid.uuid4().hex[:8]}",
        description="Member role",
        organization_id=org.id,
    )
    session.add(role)
    await session.commit()
    return role


@pytest.fixture
async def inviter(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"inviter-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _add_invitation(
    session: AsyncSession,
    org: Organization,
    role: DBRole,
    inviter: User,
    *,
    status: InvitationStatus = InvitationStatus.PENDING,
    expires_in: timedelta = timedelta(days=7),
    email_claimed_at: datetime | None = None,
    email_attempts: int = 0,
) -> OrganizationInvitation:
    invitation = OrganizationInvitation(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=f"invitee-{uuid.uuid4().hex[:8]}@example.com",
        status=status,
        invited_by=inviter.id,
        role_id=role.id,
        token=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + expires_in,
        email_claimed_at=email_claimed_at,
        email_attempts=email_attempts,
    )
    session.add(invitation)
    await session.commit()
    return invitation


async def _reload(
    session: AsyncSession, invitation_id: uuid.UUID
) -> OrganizationInvitation:
    result = await session.execute(
        select(OrganizationInvitation).where(OrganizationInvitation.id == invitation_id)
    )
    row = result.scalar_one()
    await session.refresh(row)
    return row


@pytest.mark.anyio
async def test_fresh_invitation_is_claimed_and_sent(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation = await _add_invitation(session, org, org_role, inviter)
    transport = FakeTransport()
    _patch_transport(monkeypatch, transport)

    claimed = await run_invitation_email_tick()

    assert claimed == 1
    assert len(transport.sent) == 1
    assert transport.sent[0].to == (invitation.email,)
    assert org.name in transport.sent[0].subject

    row = await _reload(session, invitation.id)
    assert row.email_sent_at is not None
    assert row.email_claimed_at is not None
    assert row.email_attempts == 1


@pytest.mark.anyio
async def test_expired_non_pending_and_claimed_are_never_claimed(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
) -> None:
    await _add_invitation(
        session, org, org_role, inviter, expires_in=timedelta(days=-1)
    )
    await _add_invitation(
        session, org, org_role, inviter, status=InvitationStatus.ACCEPTED
    )
    await _add_invitation(
        session, org, org_role, inviter, status=InvitationStatus.REVOKED
    )
    await _add_invitation(
        session, org, org_role, inviter, email_claimed_at=datetime.now(UTC)
    )
    await _add_invitation(
        session, org, org_role, inviter, email_attempts=MAX_EMAIL_ATTEMPTS
    )

    transport = AsyncMock(spec=SMTPTransport)
    assert not await deliver_next_invitation(session, transport)
    transport.send.assert_not_awaited()


@pytest.mark.anyio
async def test_unconfigured_smtp_claims_nothing(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_unconfigured: None,
) -> None:
    invitation = await _add_invitation(session, org, org_role, inviter)

    assert await run_invitation_email_tick() == 0

    row = await _reload(session, invitation.id)
    assert row.email_claimed_at is None
    assert row.email_attempts == 0


@pytest.mark.anyio
async def test_retryable_failure_releases_the_claim_until_the_attempt_cap(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation = await _add_invitation(session, org, org_role, inviter)
    failing = FakeTransport(
        error=EmailDeliveryError("SMTP delivery failed", retryable=True)
    )
    _patch_transport(monkeypatch, failing)

    # A deferred row ends the tick so the same relay is not hammered.
    assert await run_invitation_email_tick() == 0
    row = await _reload(session, invitation.id)
    assert row.email_claimed_at is None
    assert row.email_sent_at is None
    assert row.email_attempts == 1

    # A later tick with a healthy relay delivers the released row.
    healthy = FakeTransport()
    _patch_transport(monkeypatch, healthy)
    assert await run_invitation_email_tick() == 1
    row = await _reload(session, invitation.id)
    assert row.email_sent_at is not None
    assert row.email_attempts == 2


@pytest.mark.anyio
async def test_retryable_failures_stop_at_the_attempt_cap(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation = await _add_invitation(session, org, org_role, inviter)
    _patch_transport(
        monkeypatch,
        FakeTransport(error=EmailDeliveryError("nope", retryable=True)),
    )

    for _ in range(MAX_EMAIL_ATTEMPTS):
        assert await run_invitation_email_tick() == 0

    row = await _reload(session, invitation.id)
    assert row.email_attempts == MAX_EMAIL_ATTEMPTS
    assert row.email_sent_at is None
    # Released but over the cap, so no further tick picks it up.
    assert row.email_claimed_at is None
    assert await run_invitation_email_tick() == 0


@pytest.mark.anyio
@pytest.mark.parametrize("previously_sent", [False, True])
async def test_non_retryable_failure_leaves_the_row_claimed_forever(
    previously_sent: bool,
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation = await _add_invitation(session, org, org_role, inviter)
    emailed_at = datetime.now(UTC) - timedelta(minutes=5) if previously_sent else None
    invitation.email_sent_at = emailed_at
    await session.commit()
    _patch_transport(
        monkeypatch,
        FakeTransport(error=EmailDeliveryError("rejected", retryable=False)),
    )
    invitation_id = invitation.id

    assert await run_invitation_email_tick() == 1

    row = await _reload(session, invitation_id)
    assert row.email_claimed_at is not None
    assert row.email_sent_at == emailed_at
    assert row.email_attempts == 1
    assert await run_invitation_email_tick() == 0


@pytest.mark.anyio
async def test_failed_delivery_does_not_strand_remaining_batch(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitations = [
        await _add_invitation(session, org, org_role, inviter) for _ in range(3)
    ]
    invitation_ids = [invitation.id for invitation in invitations]

    class FailFirstTransport(FakeTransport):
        failed = False

        async def send(self, message: OutboundEmail) -> None:
            if not self.failed:
                self.failed = True
                raise EmailDeliveryError("ambiguous delivery")
            await super().send(message)

    transport = FailFirstTransport()
    _patch_transport(monkeypatch, transport)

    assert await run_invitation_email_tick() == 3
    assert len(transport.sent) == 2
    rows = [await _reload(session, row_id) for row_id in invitation_ids]
    assert all(row.email_claimed_at is not None for row in rows)
    assert all(row.email_attempts == 1 for row in rows)
    assert sum(row.email_sent_at is not None for row in rows) == 2
    assert await run_invitation_email_tick() == 0


@pytest.mark.anyio
async def test_cancel_mid_send_strands_only_the_in_flight_row(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitations = [
        await _add_invitation(session, org, org_role, inviter) for _ in range(3)
    ]
    invitation_ids = [invitation.id for invitation in invitations]

    class CancelledTransport(FakeTransport):
        async def send(self, message: OutboundEmail) -> None:
            # The claim must already be committed before SMTP starts.
            assert not session.in_transaction()
            raise asyncio.CancelledError

    _patch_transport(monkeypatch, CancelledTransport())
    with pytest.raises(asyncio.CancelledError):
        await run_invitation_email_tick()

    rows = [await _reload(session, row_id) for row_id in invitation_ids]
    assert sum(row.email_claimed_at is not None for row in rows) == 1
    assert sum(row.email_attempts for row in rows) == 1

    healthy = FakeTransport()
    _patch_transport(monkeypatch, healthy)
    assert await run_invitation_email_tick() == 2
    assert len(healthy.sent) == 2


@pytest.mark.anyio
async def test_stop_signal_ends_the_batch_before_the_next_claim(
    session: AsyncSession,
    org: Organization,
    org_role: DBRole,
    inviter: User,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitations = [
        await _add_invitation(session, org, org_role, inviter) for _ in range(3)
    ]
    invitation_ids = [invitation.id for invitation in invitations]
    stop_event = asyncio.Event()

    class StoppingTransport(FakeTransport):
        async def send(self, message: OutboundEmail) -> None:
            # Drain signals mid-send: finish this one, claim nothing more.
            stop_event.set()
            await super().send(message)

    transport = StoppingTransport()
    _patch_transport(monkeypatch, transport)
    assert await run_invitation_email_tick(stop_event) == 1
    assert len(transport.sent) == 1

    rows = [await _reload(session, row_id) for row_id in invitation_ids]
    assert sum(row.email_claimed_at is not None for row in rows) == 1
    assert sum(row.email_sent_at is not None for row in rows) == 1
    assert sum(row.email_attempts for row in rows) == 1


@pytest.fixture
async def committed_org(
    org_factory_session: AsyncSession,
) -> AsyncGenerator[tuple[Organization, DBRole, User], None]:
    """Real committed rows: the savepoint `session` fixture is invisible to
    the independent connections the concurrency test needs."""
    session = org_factory_session
    org = Organization(
        id=uuid.uuid4(),
        name="Concurrent Org",
        slug=f"concurrent-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug=f"organization-member-{uuid.uuid4().hex[:8]}",
        description="Member role",
        organization_id=org.id,
    )
    user = User(
        id=uuid.uuid4(),
        email=f"inviter-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add_all([org, user])
    await session.commit()
    session.add(role)
    await session.commit()
    try:
        yield org, role, user
    finally:
        await session.delete(role)
        await session.delete(org)
        await session.delete(user)
        await session.commit()


@pytest.fixture
async def org_factory_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.mark.anyio
async def test_concurrent_ticks_deliver_each_invitation_once(
    org_factory_session: AsyncSession,
    committed_org: tuple[Organization, DBRole, User],
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org, role, user = committed_org
    invitations = [
        await _add_invitation(org_factory_session, org, role, user) for _ in range(6)
    ]
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    transport = FakeTransport()
    _patch_transport(monkeypatch, transport)

    @contextlib.asynccontextmanager
    async def independent_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(
        "tracecat.invitations.consumer.get_async_session_bypass_rls_context_manager",
        independent_session,
    )
    try:
        await asyncio.gather(run_invitation_email_tick(), run_invitation_email_tick())
    finally:
        await engine.dispose()

    # SKIP LOCKED hands each row to one poller, so no email is doubled.
    assert sorted(m.to[0] for m in transport.sent) == sorted(
        invitation.email for invitation in invitations
    )

    for invitation in invitations:
        await org_factory_session.delete(invitation)
    await org_factory_session.commit()


@pytest.fixture
async def resendable_invitation(
    org_factory_session: AsyncSession,
    committed_org: tuple[Organization, DBRole, User],
) -> AsyncGenerator[OrganizationInvitation, None]:
    org, role, user = committed_org
    claimed_at = datetime.now(UTC) - RESEND_COOLDOWN * 2
    invitation = await _add_invitation(
        org_factory_session,
        org,
        role,
        user,
        email_claimed_at=claimed_at,
        email_attempts=1,
    )
    invitation.created_by_platform_admin = True
    await org_factory_session.commit()
    try:
        yield invitation
    finally:
        await org_factory_session.delete(invitation)
        await org_factory_session.commit()


async def _resend(
    session: AsyncSession,
    invitation: OrganizationInvitation,
    *,
    platform_admin: bool,
) -> None:
    """Exercise both scoped service entrypoints with the same interleaving."""
    assert invitation.invited_by is not None
    if platform_admin:
        service = AdminOrgService(
            session,
            PlatformRole(
                type="user", user_id=invitation.invited_by, service_id="tracecat-api"
            ),
        )
        await service.resend_organization_invitation(
            invitation.organization_id, invitation.id
        )
    else:
        org_service = OrgService(
            session,
            role=Role(
                type="user",
                user_id=invitation.invited_by,
                organization_id=invitation.organization_id,
                service_id="tracecat-api",
                scopes=frozenset({"org:member:invite"}),
            ),
        )
        await org_service.resend_invitation(invitation.id)


@pytest.mark.anyio
@pytest.mark.parametrize("platform_admin", [False, True])
async def test_stale_resend_cannot_clear_a_new_claim(
    org_factory_session: AsyncSession,
    resendable_invitation: OrganizationInvitation,
    smtp_configured: None,
    platform_admin: bool,
) -> None:
    invitation = resendable_invitation
    async with AsyncSession(
        org_factory_session.bind, expire_on_commit=False
    ) as stale_session:
        stale = await stale_session.get(OrganizationInvitation, invitation.id)
        assert stale is not None
        old_claim = stale.email_claimed_at

        await _resend(org_factory_session, invitation, platform_admin=platform_admin)
        transport = AsyncMock(spec=SMTPTransport)
        assert await deliver_next_invitation(org_factory_session, transport)
        assert stale.email_claimed_at == old_claim

        with pytest.raises(TracecatConflictError):
            await _resend(stale_session, invitation, platform_admin=platform_admin)

    await org_factory_session.refresh(invitation)
    assert invitation.email_claimed_at is not None
    assert invitation.email_attempts == 1


@pytest.mark.anyio
@pytest.mark.parametrize("platform_admin", [False, True])
async def test_resend_is_rejected_while_delivery_is_in_flight(
    org_factory_session: AsyncSession,
    resendable_invitation: OrganizationInvitation,
    smtp_configured: None,
    monkeypatch: pytest.MonkeyPatch,
    platform_admin: bool,
) -> None:
    invitation = resendable_invitation
    sending, finish_send = asyncio.Event(), asyncio.Event()

    async def send(message: OutboundEmail) -> None:
        sending.set()
        await finish_send.wait()

    monkeypatch.setattr(SMTPTransport, "send", AsyncMock(side_effect=send))
    transport = SMTPTransport.from_config()
    assert transport is not None
    await _resend(org_factory_session, invitation, platform_admin=platform_admin)

    async with (
        AsyncSession(org_factory_session.bind, expire_on_commit=False) as sender,
        AsyncSession(org_factory_session.bind, expire_on_commit=False) as resender,
    ):
        delivery_task = asyncio.create_task(deliver_next_invitation(sender, transport))
        try:
            await asyncio.wait_for(sending.wait(), timeout=5)
            # The committed claim is the gate: no row lock is held during SMTP.
            with pytest.raises(TracecatConflictError):
                await _resend(resender, invitation, platform_admin=platform_admin)
        finally:
            finish_send.set()
            assert await delivery_task

    await org_factory_session.refresh(invitation)
    assert invitation.email_claimed_at is not None
    assert invitation.email_sent_at is not None
