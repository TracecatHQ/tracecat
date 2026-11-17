"""Tests for the invitation table's pending-row uniqueness index."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    Invitation,
    Organization,
)
from tracecat.invitations.enums import InvitationStatus


def _token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex[:32]


async def _org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Test Organization",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.flush()
    return org


class TestInvitation:
    """Tests for the org-anchored Invitation model."""

    @pytest.mark.anyio
    async def test_duplicate_pending_rows_rejected_by_the_database(
        self, session: AsyncSession
    ):
        """A partial unique index, not the app, enforces one pending row per email."""
        org = await _org(session)

        session.add(
            Invitation(
                organization_id=org.id,
                email="Same@Example.com",
                status=InvitationStatus.PENDING,
                token=_token(),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await session.flush()
        # Case-insensitive: the index is on lower(email).
        session.add(
            Invitation(
                organization_id=org.id,
                email="same@example.com",
                status=InvitationStatus.PENDING,
                token=_token(),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()

    @pytest.mark.anyio
    async def test_pending_uniqueness_ignores_settled_rows(self, session: AsyncSession):
        """A revoked invitation does not block a new pending one."""
        org = await _org(session)

        session.add(
            Invitation(
                organization_id=org.id,
                email="again@example.com",
                status=InvitationStatus.REVOKED,
                token=_token(),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await session.flush()
        session.add(
            Invitation(
                organization_id=org.id,
                email="again@example.com",
                status=InvitationStatus.PENDING,
                token=_token(),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await session.commit()

        rows = (
            (
                await session.execute(
                    select(Invitation).where(Invitation.organization_id == org.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
