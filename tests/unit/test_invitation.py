"""Tests for the unified invitation model and its grants."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.db.models import (
    Invitation,
    InvitationGrant,
    Organization,
    Role,
    User,
    Workspace,
)
from tracecat.invitations.enums import InvitationStatus


def _create_role(
    organization_id: uuid.UUID,
    *,
    name: str = "Organization Member",
    slug: str = "organization-member",
) -> Role:
    """Create a Role DB record for testing."""
    return Role(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        organization_id=organization_id,
    )


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


async def _workspace(session: AsyncSession, organization_id: uuid.UUID) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(),
        name=f"ws-{uuid.uuid4().hex[:8]}",
        organization_id=organization_id,
        settings={},
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def _user(session: AsyncSession) -> User:
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
    await session.flush()
    return user


class TestInvitation:
    """Tests for the org-anchored Invitation model."""

    @pytest.mark.anyio
    async def test_create_org_invitation(self, session: AsyncSession):
        """An invitation with no workspace carries an org-wide grant."""
        org = await _org(session)
        inviter = await _user(session)
        member_role = _create_role(org.id)
        session.add(member_role)
        await session.flush()

        token = _token()
        expires_at = datetime.now(UTC) + timedelta(days=7)
        invitation = Invitation(
            organization_id=org.id,
            workspace_id=None,
            email="invitee@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            invited_by=inviter.id,
            token=token,
            expires_at=expires_at,
        )
        session.add(invitation)
        await session.flush()
        session.add(
            InvitationGrant(
                organization_id=org.id,
                invitation_id=invitation.id,
                workspace_id=None,
                role_id=member_role.id,
            )
        )
        await session.commit()
        await session.refresh(invitation)

        assert invitation.id is not None
        assert invitation.organization_id == org.id
        assert invitation.workspace_id is None
        assert invitation.email == "invitee@example.com"
        assert invitation.status == InvitationStatus.PENDING
        assert invitation.invited_by == inviter.id
        assert invitation.token == token
        assert invitation.accepted_at is None
        assert invitation.created_by_platform_admin is False
        assert invitation.created_at is not None

        grants = (
            (
                await session.execute(
                    select(InvitationGrant).where(
                        InvitationGrant.invitation_id == invitation.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(grants) == 1
        assert grants[0].workspace_id is None
        assert grants[0].role_id == member_role.id

    @pytest.mark.anyio
    async def test_invitation_carries_multiple_grants(self, session: AsyncSession):
        """One invitation holds an org grant plus a grant per workspace."""
        org = await _org(session)
        ws_a = await _workspace(session, org.id)
        ws_b = await _workspace(session, org.id)
        member_role = _create_role(org.id)
        editor_role = _create_role(
            org.id, name="Workspace Editor", slug="workspace-editor"
        )
        session.add_all([member_role, editor_role])
        await session.flush()

        invitation = Invitation(
            organization_id=org.id,
            workspace_id=ws_a.id,
            email="multi@example.com",
            role_id=editor_role.id,
            status=InvitationStatus.PENDING,
            token=_token(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.flush()
        session.add_all(
            [
                InvitationGrant(
                    organization_id=org.id,
                    invitation_id=invitation.id,
                    workspace_id=None,
                    role_id=member_role.id,
                ),
                InvitationGrant(
                    organization_id=org.id,
                    invitation_id=invitation.id,
                    workspace_id=ws_a.id,
                    role_id=editor_role.id,
                ),
                InvitationGrant(
                    organization_id=org.id,
                    invitation_id=invitation.id,
                    workspace_id=ws_b.id,
                    role_id=editor_role.id,
                ),
            ]
        )
        await session.commit()

        grants = (
            (
                await session.execute(
                    select(InvitationGrant).where(
                        InvitationGrant.invitation_id == invitation.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(grants) == 3
        assert {g.workspace_id for g in grants} == {None, ws_a.id, ws_b.id}

    @pytest.mark.anyio
    async def test_one_org_grant_per_invitation(self, session: AsyncSession):
        """A second org-wide grant on one invitation is rejected."""
        org = await _org(session)
        member_role = _create_role(org.id)
        session.add(member_role)
        await session.flush()

        invitation = Invitation(
            organization_id=org.id,
            workspace_id=None,
            email="dupe-grant@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            token=_token(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.flush()
        session.add_all(
            [
                InvitationGrant(
                    organization_id=org.id,
                    invitation_id=invitation.id,
                    workspace_id=None,
                    role_id=member_role.id,
                ),
                InvitationGrant(
                    organization_id=org.id,
                    invitation_id=invitation.id,
                    workspace_id=None,
                    role_id=member_role.id,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    @pytest.mark.anyio
    async def test_duplicate_pending_rows_are_allowed_by_the_database(
        self, session: AsyncSession
    ):
        """The migration copies duplicate pending rows, so the table permits them.

        One pending invitation per email is enforced in
        ``InvitationService.create_invitation``, not by a constraint.
        """
        org = await _org(session)
        member_role = _create_role(org.id)
        session.add(member_role)
        await session.flush()

        for _ in range(2):
            session.add(
                Invitation(
                    organization_id=org.id,
                    workspace_id=None,
                    email="Same@Example.com",
                    role_id=member_role.id,
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

    @pytest.mark.anyio
    async def test_pending_uniqueness_ignores_settled_rows(self, session: AsyncSession):
        """A revoked invitation does not block a new pending one."""
        org = await _org(session)
        member_role = _create_role(org.id)
        session.add(member_role)
        await session.flush()

        session.add(
            Invitation(
                organization_id=org.id,
                workspace_id=None,
                email="again@example.com",
                role_id=member_role.id,
                status=InvitationStatus.REVOKED,
                token=_token(),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await session.flush()
        session.add(
            Invitation(
                organization_id=org.id,
                workspace_id=None,
                email="again@example.com",
                role_id=member_role.id,
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

    @pytest.mark.anyio
    async def test_invitation_status_transition(self, session: AsyncSession):
        """Accepting stamps accepted_at."""
        org = await _org(session)
        member_role = _create_role(org.id)
        session.add(member_role)
        await session.flush()

        invitation = Invitation(
            organization_id=org.id,
            workspace_id=None,
            email="transition@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            token=_token(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.commit()

        accepted_at = datetime.now(UTC)
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = accepted_at
        await session.commit()
        await session.refresh(invitation)

        assert invitation.status == InvitationStatus.ACCEPTED
        assert invitation.accepted_at is not None

    @pytest.mark.anyio
    async def test_invitation_cascade_delete_from_org(self, session: AsyncSession):
        """Deleting the org removes its invitations and their grants."""
        org = await _org(session)
        member_role = _create_role(org.id)
        session.add(member_role)
        await session.flush()

        invitation = Invitation(
            organization_id=org.id,
            workspace_id=None,
            email="cascade@example.com",
            role_id=member_role.id,
            status=InvitationStatus.PENDING,
            token=_token(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.flush()
        session.add(
            InvitationGrant(
                organization_id=org.id,
                invitation_id=invitation.id,
                workspace_id=None,
                role_id=member_role.id,
            )
        )
        await session.commit()
        invitation_id = invitation.id

        await session.delete(org)
        await session.commit()

        assert (
            await session.execute(
                select(Invitation).where(Invitation.id == invitation_id)
            )
        ).scalar_one_or_none() is None
        assert (
            await session.execute(
                select(InvitationGrant).where(
                    InvitationGrant.invitation_id == invitation_id
                )
            )
        ).scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_grants_cascade_delete_from_invitation(self, session: AsyncSession):
        """Deleting an invitation removes its grants."""
        org = await _org(session)
        ws = await _workspace(session, org.id)
        editor_role = _create_role(
            org.id, name="Workspace Editor", slug="workspace-editor"
        )
        session.add(editor_role)
        await session.flush()

        invitation = Invitation(
            organization_id=org.id,
            workspace_id=ws.id,
            email="grant-cascade@example.com",
            role_id=editor_role.id,
            status=InvitationStatus.PENDING,
            token=_token(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.flush()
        session.add(
            InvitationGrant(
                organization_id=org.id,
                invitation_id=invitation.id,
                workspace_id=ws.id,
                role_id=editor_role.id,
            )
        )
        await session.commit()
        invitation_id = invitation.id

        await session.delete(invitation)
        await session.commit()

        assert (
            await session.execute(
                select(InvitationGrant).where(
                    InvitationGrant.invitation_id == invitation_id
                )
            )
        ).scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_invitation_unique_token(self, session: AsyncSession):
        """Two invitations cannot share a token."""
        org = await _org(session)
        member_role = _create_role(org.id)
        session.add(member_role)
        await session.flush()

        token = _token()
        session.add_all(
            [
                Invitation(
                    organization_id=org.id,
                    workspace_id=None,
                    email="first@example.com",
                    role_id=member_role.id,
                    status=InvitationStatus.PENDING,
                    token=token,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                ),
                Invitation(
                    organization_id=org.id,
                    workspace_id=None,
                    email="second@example.com",
                    role_id=member_role.id,
                    status=InvitationStatus.PENDING,
                    token=token,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
