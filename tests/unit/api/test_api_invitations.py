"""HTTP-level tests for organization invitation endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.db.engine import get_async_session
from tracecat.invitations.schemas import InvitationGrant


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
    role_id = uuid.uuid4()
    mock_invitation.id = uuid.uuid4()
    # Grants arrive as ORM rows now, so the mock exposes attributes.
    mock_invitation.grants = [SimpleNamespace(workspace_id=None, role_id=role_id)]

    tuples_result = Mock()
    tuples_result.all.return_value = [
        (mock_invitation, mock_organization, mock_inviter),
    ]
    pending_result = Mock()
    pending_result.tuples.return_value = tuples_result

    mock_session.execute.side_effect = [pending_result]
    app.dependency_overrides[current_active_user] = lambda: mock_user

    try:
        response = client.get("/invitations/pending/me")
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
    assert payload[0]["grants"] == [{"workspace_id": None, "role_id": str(role_id)}]


def test_grant_json_validates_into_uuids() -> None:
    """Grant rows read through from_attributes; a null workspace stays None."""
    role_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    grants = TypeAdapter(list[InvitationGrant]).validate_python(
        [
            SimpleNamespace(workspace_id=None, role_id=role_id),
            SimpleNamespace(workspace_id=workspace_id, role_id=role_id),
        ]
    )
    assert grants[0].workspace_id is None
    assert grants[0].role_id == role_id
    assert grants[1].workspace_id == workspace_id


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
        response = client.get("/invitations/pending/me")
    finally:
        app.dependency_overrides.pop(current_active_user, None)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
