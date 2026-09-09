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

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.auth.schemas import UserRole
from tracecat.db.models import Organization, OrganizationInvitation, User
from tracecat.db.models import Role as DBRole
from tracecat.email.transport import EmailDeliveryError, OutboundEmail, SMTPTransport
from tracecat.invitations.consumer import (
    MAX_EMAIL_ATTEMPTS,
    deliver_next_invitation,
    run_invitation_email_tick,
)
from tracecat.invitations.enums import InvitationStatus


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


@pytest.fixture
def smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PORT", 587)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", "relay")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", "secret")
    monkeypatch.setattr(
        config, "TRACECAT__EMAIL_FROM", "Tracecat <no-reply@example.com>"
    )


@pytest.fixture
def smtp_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", None)
    monkeypatch.setattr(config, "TRACECAT__EMAIL_FROM", None)


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
async def test_non_retryable_failure_leaves_the_row_claimed_forever(
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
        FakeTransport(error=EmailDeliveryError("rejected", retryable=False)),
    )
    invitation_id = invitation.id

    assert await run_invitation_email_tick() == 1

    row = await _reload(session, invitation_id)
    assert row.email_claimed_at is not None
    assert row.email_sent_at is None
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
