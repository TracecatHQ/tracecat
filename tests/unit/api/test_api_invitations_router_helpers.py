"""Unit tests for invitation router response helpers."""

import uuid
from datetime import UTC, datetime, timedelta

from tracecat.db.models import Invitation
from tracecat.db.models import Role as DBRole
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.router import _invitation_to_read


def _mock_invitation() -> Invitation:
    now = datetime.now(UTC)
    invitation = Invitation(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        email="invitee@example.com",
        role_id=uuid.uuid4(),
        status=InvitationStatus.PENDING,
        invited_by=uuid.uuid4(),
        expires_at=now + timedelta(days=7),
        accepted_at=None,
        token="super-secret-token",
    )
    invitation.created_at = now
    invitation.role_obj = DBRole(
        id=invitation.role_id,
        organization_id=invitation.organization_id,
        name="Workspace Admin",
        slug="workspace-admin",
        description=None,
    )
    return invitation


def test_invitation_to_read_excludes_token_by_default() -> None:
    invitation = _mock_invitation()

    response = _invitation_to_read(invitation)

    assert response.token is None


def test_invitation_to_read_includes_token_when_requested() -> None:
    invitation = _mock_invitation()

    response = _invitation_to_read(invitation, include_token=True)

    assert response.token == invitation.token
