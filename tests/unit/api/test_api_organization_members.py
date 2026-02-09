"""HTTP-level tests for organization members API endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.authz.enums import OrgRole
from tracecat.organization import router as organization_router


def _member_user(user_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        email="member@example.com",
        first_name="Member",
        last_name="User",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        last_login_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_list_org_members_omits_superuser_flag(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [(user, OrgRole.ADMIN)]
        MockService.return_value = mock_svc

        response = client.get("/organization/members")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == str(user.id)
    assert "is_superuser" not in data[0]


@pytest.mark.anyio
async def test_update_org_member_omits_superuser_flag(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_member.return_value = (user, OrgRole.MEMBER)
        MockService.return_value = mock_svc

        response = client.patch(
            f"/organization/members/{user.id}",
            json={"role": "basic"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["user_id"] == str(user.id)
    assert data["role"] == "member"
    assert "is_superuser" not in data
