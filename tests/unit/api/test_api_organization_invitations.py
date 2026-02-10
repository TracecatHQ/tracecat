"""HTTP-level tests for organization invitation endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.authz.enums import OrgRole
from tracecat.db.engine import get_async_session


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
        role=OrgRole.MEMBER,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        created_at=datetime.now(UTC),
    )
    mock_organization = SimpleNamespace(name="Acme Security")

    tuples_result = Mock()
    tuples_result.all.return_value = [
        (mock_invitation, mock_organization, mock_inviter),
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
    assert payload[0]["role"] == "member"


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
