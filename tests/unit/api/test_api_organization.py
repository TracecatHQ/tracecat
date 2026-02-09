"""HTTP-level tests for organization router.

Tests the organization endpoints for members, sessions, and invitations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.types import Role
from tracecat.authz.enums import OrgRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization import router as org_router


def _mock_user(
    user_id: uuid.UUID | None = None,
    email: str = "user@test.com",
    first_name: str = "Test",
    last_name: str = "User",
    is_active: bool = True,
    is_superuser: bool = False,
    is_verified: bool = True,
    last_login_at: datetime | None = None,
) -> MagicMock:
    user = MagicMock()
    user.id = user_id or uuid.uuid4()
    user.email = email
    user.first_name = first_name
    user.last_name = last_name
    user.is_active = is_active
    user.is_superuser = is_superuser
    user.is_verified = is_verified
    user.last_login_at = last_login_at
    return user


def _mock_invitation(
    invitation_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    email: str = "invitee@test.com",
    role: OrgRole = OrgRole.MEMBER,
    invitation_status: InvitationStatus = InvitationStatus.PENDING,
    invited_by: uuid.UUID | None = None,
    token: str = "test-token-123",
) -> MagicMock:
    inv = MagicMock()
    inv.id = invitation_id or uuid.uuid4()
    inv.organization_id = organization_id or uuid.uuid4()
    inv.email = email
    inv.role = role
    inv.status = invitation_status
    inv.invited_by = invited_by
    inv.expires_at = datetime.now(UTC) + timedelta(days=7)
    inv.created_at = datetime.now(UTC)
    inv.accepted_at = None
    inv.token = token
    return inv


@pytest.mark.anyio
class TestListMembers:
    """Test listing organization members."""

    async def test_list_members_empty(
        self, client: TestClient, test_role: Role
    ) -> None:
        """List members should return empty list when none exist."""
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = []

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.get("/organization/members")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    async def test_list_members_returns_members(
        self, client: TestClient, test_role: Role
    ) -> None:
        """List members should return member data."""
        user = _mock_user(email="member@test.com")
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [(user, OrgRole.MEMBER)]

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.get("/organization/members")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["email"] == "member@test.com"
        assert data[0]["role"] == OrgRole.MEMBER.value


@pytest.mark.anyio
class TestDeleteMember:
    """Test deleting organization members."""

    async def test_delete_member_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Delete member should return 204."""
        user_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.delete_member.return_value = None

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/members/{user_id}")

        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_delete_member_not_found(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Delete non-existent member should return 404."""
        user_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.delete_member.side_effect = NoResultFound()

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/members/{user_id}")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_delete_member_forbidden(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Delete superuser member should return 403."""
        user_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.delete_member.side_effect = TracecatAuthorizationError(
            "Cannot delete superuser"
        )

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/members/{user_id}")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_delete_member_integrity_error(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Delete member with active sessions should return 400."""
        user_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.delete_member.side_effect = IntegrityError(
            "fk constraint", params=None, orig=Exception()
        )

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/members/{user_id}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
class TestUpdateMember:
    """Test updating organization members."""

    async def test_update_member_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Update member should return updated member data."""
        user_id = uuid.uuid4()
        user = _mock_user(user_id=user_id, first_name="Updated")
        mock_svc = AsyncMock()
        mock_svc.update_member.return_value = (user, OrgRole.ADMIN)

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.patch(
                f"/organization/members/{user_id}",
                json={"first_name": "Updated"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["first_name"] == "Updated"

    async def test_update_member_not_found(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Update non-existent member should return 404."""
        user_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.update_member.side_effect = NoResultFound()

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.patch(
                f"/organization/members/{user_id}",
                json={"first_name": "Updated"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_update_member_forbidden(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Update superuser member should return 403."""
        user_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.update_member.side_effect = TracecatAuthorizationError(
            "Cannot update superuser"
        )

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.patch(
                f"/organization/members/{user_id}",
                json={"first_name": "Updated"},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
class TestSessions:
    """Test session management endpoints."""

    async def test_list_sessions_empty(
        self, client: TestClient, test_role: Role
    ) -> None:
        """List sessions should return empty list."""
        mock_svc = AsyncMock()
        mock_svc.list_sessions.return_value = []

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.get("/organization/sessions")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    async def test_delete_session_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Delete session should return 204."""
        session_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.delete_session.return_value = None

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/sessions/{session_id}")

        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_delete_session_not_found(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Delete non-existent session should return 404."""
        session_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.delete_session.side_effect = NoResultFound()

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/sessions/{session_id}")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
class TestCreateInvitation:
    """Test creating organization invitations."""

    async def test_create_invitation_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Create invitation should return 201 with invitation data."""
        inv = _mock_invitation()
        mock_svc = AsyncMock()
        mock_svc.create_invitation.return_value = inv

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.post(
                "/organization/invitations",
                json={"email": "invitee@test.com", "role": "member"},
            )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["email"] == "invitee@test.com"
        assert data["status"] == InvitationStatus.PENDING.value

    async def test_create_invitation_forbidden(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Create owner invitation as non-owner should return 403."""
        mock_svc = AsyncMock()
        mock_svc.create_invitation.side_effect = TracecatAuthorizationError(
            "Only owners can create owner invitations"
        )

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.post(
                "/organization/invitations",
                json={"email": "invitee@test.com", "role": "owner"},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_create_invitation_validation_error(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Create invitation with validation error should return 400."""
        mock_svc = AsyncMock()
        mock_svc.create_invitation.side_effect = TracecatValidationError(
            "Duplicate invitation"
        )

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.post(
                "/organization/invitations",
                json={"email": "invitee@test.com"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_create_invitation_conflict(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Create duplicate invitation should return 409."""
        mock_svc = AsyncMock()
        mock_svc.create_invitation.side_effect = IntegrityError(
            "unique constraint", params=None, orig=Exception()
        )

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.post(
                "/organization/invitations",
                json={"email": "invitee@test.com"},
            )

        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
class TestListInvitations:
    """Test listing invitations."""

    async def test_list_invitations_empty(
        self, client: TestClient, test_role: Role
    ) -> None:
        """List invitations should return empty list."""
        mock_svc = AsyncMock()
        mock_svc.list_invitations.return_value = []

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.get("/organization/invitations")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    async def test_list_invitations_with_status_filter(
        self, client: TestClient, test_role: Role
    ) -> None:
        """List invitations with status filter should pass filter to service."""
        inv = _mock_invitation()
        mock_svc = AsyncMock()
        mock_svc.list_invitations.return_value = [inv]

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.get(
                "/organization/invitations",
                params={"status": "pending"},
            )

        assert response.status_code == status.HTTP_200_OK
        mock_svc.list_invitations.assert_awaited_once_with(
            status=InvitationStatus.PENDING
        )


@pytest.mark.anyio
class TestRevokeInvitation:
    """Test revoking invitations."""

    async def test_revoke_invitation_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Revoke invitation should return 204."""
        invitation_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.revoke_invitation.return_value = _mock_invitation()

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/invitations/{invitation_id}")

        assert response.status_code == status.HTTP_204_NO_CONTENT

    async def test_revoke_invitation_not_found(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Revoke non-existent invitation should return 404."""
        invitation_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.revoke_invitation.side_effect = NoResultFound()

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/invitations/{invitation_id}")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_revoke_invitation_forbidden(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Revoke invitation without permission should return 403."""
        invitation_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.revoke_invitation.side_effect = TracecatAuthorizationError(
            "Not authorized"
        )

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.delete(f"/organization/invitations/{invitation_id}")

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
class TestGetInvitationToken:
    """Test getting invitation tokens."""

    async def test_get_invitation_token_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Get invitation token should return token string."""
        invitation_id = uuid.uuid4()
        inv = _mock_invitation(invitation_id=invitation_id, token="secret-token")
        mock_svc = AsyncMock()
        mock_svc.get_invitation.return_value = inv

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.get(f"/organization/invitations/{invitation_id}/token")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"token": "secret-token"}

    async def test_get_invitation_token_not_found(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Get token for non-existent invitation should return 404."""
        invitation_id = uuid.uuid4()
        mock_svc = AsyncMock()
        mock_svc.get_invitation.side_effect = NoResultFound()

        with patch.object(org_router, "OrgService", return_value=mock_svc):
            response = client.get(f"/organization/invitations/{invitation_id}/token")

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
class TestAcceptInvitation:
    """Test accepting invitations."""

    async def test_accept_invitation_success(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Accept invitation should return success message."""
        mock_membership = MagicMock()

        with patch.object(
            org_router,
            "accept_invitation_for_user",
            new_callable=AsyncMock,
            return_value=mock_membership,
        ):
            response = client.post(
                "/organization/invitations/accept",
                json={"token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Invitation accepted successfully"

    async def test_accept_invitation_not_found(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Accept with invalid token should return 404."""
        with patch.object(
            org_router,
            "accept_invitation_for_user",
            new_callable=AsyncMock,
            side_effect=TracecatNotFoundError("Invitation not found"),
        ):
            response = client.post(
                "/organization/invitations/accept",
                json={"token": "invalid-token"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_accept_invitation_auth_error(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Accept with email mismatch should return 400."""
        with patch.object(
            org_router,
            "accept_invitation_for_user",
            new_callable=AsyncMock,
            side_effect=TracecatAuthorizationError("Email mismatch"),
        ):
            response = client.post(
                "/organization/invitations/accept",
                json={"token": "valid-token"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    async def test_accept_invitation_already_member(
        self, client: TestClient, test_role: Role
    ) -> None:
        """Accept when already a member should return 409."""
        with patch.object(
            org_router,
            "accept_invitation_for_user",
            new_callable=AsyncMock,
            side_effect=IntegrityError(
                "unique constraint", params=None, orig=Exception()
            ),
        ):
            response = client.post(
                "/organization/invitations/accept",
                json={"token": "valid-token"},
            )

        assert response.status_code == status.HTTP_409_CONFLICT
