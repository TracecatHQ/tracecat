"""HTTP-level tests for unified invitation endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.db.models import Invitation, Organization, User
from tracecat.db.models import Role as DBRole
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.router import InvitationGroup


@pytest.mark.anyio
async def test_list_my_pending_invitations_success(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_user = SimpleNamespace(
        id=test_admin_role.user_id,
        email="user@example.com",
    )
    mock_inviter = User(
        id=uuid.uuid4(),
        email="alice@example.com",
        hashed_password="hashed",
        role="basic",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        first_name="Alice",
        last_name="Admin",
    )
    organization_id = uuid.uuid4()
    mock_organization = Organization(
        id=organization_id,
        name="Acme Security",
        slug="acme-security",
        is_active=True,
    )
    mock_role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug="organization-member",
        description="Member role",
        organization_id=organization_id,
    )
    mock_invitation = Invitation(
        id=uuid.uuid4(),
        token="invitation-token-123",
        organization_id=organization_id,
        workspace_id=None,
        email="user@example.com",
        status=InvitationStatus.PENDING,
        role_id=mock_role.id,
        invited_by=mock_inviter.id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        accepted_at=None,
    )
    mock_inviter.first_name = "Alice"
    mock_inviter.last_name = "Admin"
    mock_organization.name = "Acme Security"
    mock_organization.slug = "acme-security"
    mock_invitation.role_obj = mock_role
    mock_invitation.inviter = mock_inviter
    mock_invitation.organization = mock_organization
    group = InvitationGroup(
        invitation=mock_invitation,
        workspace_invitations=[],
        accept_token=mock_invitation.token,
    )
    monkeypatch.setattr(
        "tracecat.invitations.router.list_pending_invitation_groups_for_email",
        AsyncMock(return_value=[group]),
    )
    app.dependency_overrides[current_active_user] = lambda: mock_user

    try:
        response = client.get("/invitations/pending/me")
    finally:
        app.dependency_overrides.pop(current_active_user, None)

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["accept_token"] == mock_invitation.token
    assert payload[0]["organization_id"] == str(organization_id)
    assert payload[0]["organization_name"] == "Acme Security"
    assert payload[0]["inviter_name"] == "Alice Admin"
    assert payload[0]["inviter_email"] == "alice@example.com"
    assert payload[0]["role_name"] == "Organization Member"
    assert payload[0]["role_slug"] == "organization-member"


@pytest.mark.anyio
async def test_list_my_pending_invitations_empty_result(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_user = SimpleNamespace(
        id=test_admin_role.user_id,
        email="user@example.com",
    )
    monkeypatch.setattr(
        "tracecat.invitations.router.list_pending_invitation_groups_for_email",
        AsyncMock(return_value=[]),
    )
    app.dependency_overrides[current_active_user] = lambda: mock_user

    try:
        response = client.get("/invitations/pending/me")
    finally:
        app.dependency_overrides.pop(current_active_user, None)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
