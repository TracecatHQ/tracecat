"""HTTP-level tests for organization invitation endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.db.engine import get_async_session
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization import router as organization_router


@pytest.mark.anyio
async def test_list_my_pending_invitations_success(
    client: TestClient, test_admin_role: Role
) -> None:
    mock_session = await app.dependency_overrides[get_async_session]()

    mock_user = SimpleNamespace(
        id=test_admin_role.user_id,
        email="user@example.com",
    )
    mock_inviter = SimpleNamespace(
        first_name="Alice",
        last_name="Admin",
        email="alice@example.com",
    )
    organization_id = uuid.uuid4()
    mock_invitation = SimpleNamespace(
        token="invitation-token-123",
        organization_id=organization_id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        created_at=datetime.now(UTC),
    )
    mock_organization = SimpleNamespace(name="Acme Security")
    mock_role = SimpleNamespace(name="Organization Member", slug="organization-member")

    tuples_result = Mock()
    tuples_result.all.return_value = [
        (mock_invitation, mock_organization, mock_inviter, mock_role),
    ]
    pending_result = Mock()
    pending_result.tuples.return_value = tuples_result

    mock_session.execute.side_effect = [pending_result]
    app.dependency_overrides[current_active_user] = lambda: mock_user

    try:
        response = client.get("/organization/invitations/pending/me")
    finally:
        app.dependency_overrides.pop(current_active_user, None)

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["token"] == mock_invitation.token
    assert payload[0]["organization_id"] == str(organization_id)
    assert payload[0]["organization_name"] == "Acme Security"
    assert payload[0]["inviter_name"] == "Alice Admin"
    assert payload[0]["inviter_email"] == "alice@example.com"
    assert payload[0]["role_name"] == "Organization Member"
    assert payload[0]["role_slug"] == "organization-member"


@pytest.mark.anyio
async def test_list_my_pending_invitations_empty_result(
    client: TestClient, test_admin_role: Role
) -> None:
    mock_session = await app.dependency_overrides[get_async_session]()
    mock_user = SimpleNamespace(
        id=test_admin_role.user_id,
        email="user@example.com",
    )

    tuples_result = Mock()
    tuples_result.all.return_value = []
    pending_result = Mock()
    pending_result.tuples.return_value = tuples_result

    mock_session.execute.side_effect = [pending_result]
    app.dependency_overrides[current_active_user] = lambda: mock_user

    try:
        response = client.get("/organization/invitations/pending/me")
    finally:
        app.dependency_overrides.pop(current_active_user, None)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def _mock_invitation(*, email_sent_at: datetime | None = None) -> SimpleNamespace:
    """A row shaped like the ORM object the resend route serializes."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="invitee@example.com",
        role_id=uuid.uuid4(),
        role_obj=SimpleNamespace(
            name="Organization Member", slug="organization-member"
        ),
        status=InvitationStatus.PENDING,
        invited_by=uuid.uuid4(),
        expires_at=now + timedelta(days=7),
        created_at=now,
        accepted_at=None,
        email_sent_at=email_sent_at,
    )


@pytest.mark.anyio
async def test_resend_invitation_success(
    client: TestClient, test_admin_role: Role
) -> None:
    invitation = _mock_invitation()

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.resend_invitation.return_value = invitation
        MockService.return_value = mock_svc

        response = client.post(f"/organization/invitations/{invitation.id}/resend")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["id"] == str(invitation.id)
    assert payload["email"] == invitation.email
    assert payload["last_emailed_at"] is None
    mock_svc.resend_invitation.assert_awaited_once_with(invitation.id)


@pytest.mark.anyio
async def test_resend_invitation_not_found(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.resend_invitation.side_effect = NoResultFound
        MockService.return_value = mock_svc

        response = client.post(f"/organization/invitations/{uuid.uuid4()}/resend")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_resend_invitation_validation_error_returns_400(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.resend_invitation.side_effect = TracecatValidationError(
            "Email delivery is not configured"
        )
        MockService.return_value = mock_svc

        response = client.post(f"/organization/invitations/{uuid.uuid4()}/resend")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Email delivery is not configured"


@pytest.mark.anyio
async def test_resend_invitation_cooldown_returns_409(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.resend_invitation.side_effect = TracecatConflictError("too soon")
        MockService.return_value = mock_svc

        response = client.post(f"/organization/invitations/{uuid.uuid4()}/resend")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert (
        response.json()["detail"] == "Invitation email was sent less than a minute ago"
    )


@pytest.mark.anyio
async def test_list_invitations_exposes_last_emailed_at(
    client: TestClient, test_admin_role: Role
) -> None:
    emailed_at = datetime.now(UTC) - timedelta(minutes=3)
    invitation = _mock_invitation(email_sent_at=emailed_at)

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_invitations.return_value = [invitation]
        MockService.return_value = mock_svc

        response = client.get("/organization/invitations")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload[0]["last_emailed_at"] is not None
