"""HTTP-level tests for admin users API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.users import router as users_router
from tracecat_ee.admin.users.schemas import AdminUserRead

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role


def _user_read(
    user_id: uuid.UUID | None = None,
    is_superuser: bool = False,
    email: str = "test@example.com",
) -> AdminUserRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return AdminUserRead(
        id=user_id or uuid.uuid4(),
        email=email,
        first_name="Test",
        last_name="User",
        role=UserRole.ADMIN,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        last_login_at=now,
    )


@pytest.mark.anyio
async def test_list_users_success(client: TestClient, test_admin_role: Role) -> None:
    user = _user_read()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_users.return_value = [user]
        MockService.return_value = mock_svc

        response = client.get("/admin/users")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == str(user.id)
    assert data[0]["email"] == user.email


@pytest.mark.anyio
async def test_get_user_success(client: TestClient, test_admin_role: Role) -> None:
    user_id = uuid.uuid4()
    user = _user_read(user_id=user_id)

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_user.return_value = user
        MockService.return_value = mock_svc

        response = client.get(f"/admin/users/{user_id}")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == str(user_id)


@pytest.mark.anyio
async def test_get_user_not_found(client: TestClient, test_admin_role: Role) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_user.side_effect = ValueError(f"User {user_id} not found")
        MockService.return_value = mock_svc

        response = client.get(f"/admin/users/{user_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_promote_user_success(client: TestClient, test_admin_role: Role) -> None:
    user_id = uuid.uuid4()
    user = _user_read(user_id=user_id, is_superuser=True)

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.promote_superuser.return_value = user
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/promote")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_superuser"] is True


@pytest.mark.anyio
async def test_promote_user_already_superuser(
    client: TestClient, test_admin_role: Role
) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.promote_superuser.side_effect = ValueError(
            f"User {user_id} is already a superuser"
        )
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/promote")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_promote_user_not_found(
    client: TestClient, test_admin_role: Role
) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.promote_superuser.side_effect = ValueError(f"User {user_id} not found")
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/promote")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_demote_user_success(client: TestClient, test_admin_role: Role) -> None:
    user_id = uuid.uuid4()
    user = _user_read(user_id=user_id, is_superuser=False)

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.demote_superuser.return_value = user
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/demote")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_superuser"] is False


@pytest.mark.anyio
async def test_demote_self_error(client: TestClient, test_admin_role: Role) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.demote_superuser.side_effect = ValueError("Cannot demote yourself")
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/demote")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot demote yourself" in response.json()["detail"]


@pytest.mark.anyio
async def test_demote_last_superuser_error(
    client: TestClient, test_admin_role: Role
) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.demote_superuser.side_effect = ValueError(
            "Cannot demote the last superuser"
        )
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/demote")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot demote the last superuser" in response.json()["detail"]


@pytest.mark.anyio
async def test_demote_non_superuser_error(
    client: TestClient, test_admin_role: Role
) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.demote_superuser.side_effect = ValueError(
            f"User {user_id} is not a superuser"
        )
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/demote")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_demote_user_not_found(client: TestClient, test_admin_role: Role) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.demote_superuser.side_effect = ValueError(f"User {user_id} not found")
        MockService.return_value = mock_svc

        response = client.post(f"/admin/users/{user_id}/demote")

    assert response.status_code == status.HTTP_404_NOT_FOUND
