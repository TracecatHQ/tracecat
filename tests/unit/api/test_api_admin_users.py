"""HTTP-level tests for admin users API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from fastapi_users import InvalidPasswordException
from tracecat_ee.admin.users import router as users_router

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError

_USER_ID = uuid.uuid4()
_NEW_USER = {"email": "new-user@example.com", "password": "short"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method", "error", "status_code", "detail"),
    [
        pytest.param(
            "POST",
            "/admin/users",
            {
                "email": "existing@example.com",
                "password": "this-is-a-strong-password",
            },
            "create_user",
            ValueError("User with email existing@example.com already exists"),
            status.HTTP_409_CONFLICT,
            None,
            id="create-conflict",
        ),
        pytest.param(
            "POST",
            "/admin/users",
            _NEW_USER,
            "create_user",
            ValueError("Password must be at least 12 characters long"),
            status.HTTP_400_BAD_REQUEST,
            None,
            id="create-value-error",
        ),
        pytest.param(
            "POST",
            "/admin/users",
            _NEW_USER,
            "create_user",
            InvalidPasswordException("Password must be at least 12 characters long"),
            status.HTTP_400_BAD_REQUEST,
            None,
            id="create-invalid-password",
        ),
        pytest.param(
            "GET",
            f"/admin/users/{_USER_ID}",
            None,
            "get_user",
            ValueError(f"User {_USER_ID} not found"),
            status.HTTP_404_NOT_FOUND,
            None,
            id="get-not-found",
        ),
        pytest.param(
            "DELETE",
            f"/admin/users/{_USER_ID}",
            None,
            "delete_user",
            TracecatNotFoundError(f"User {_USER_ID} not found"),
            status.HTTP_404_NOT_FOUND,
            None,
            id="delete-not-found",
        ),
        pytest.param(
            "DELETE",
            f"/admin/users/{_USER_ID}",
            None,
            "delete_user",
            ValueError("Cannot delete yourself"),
            status.HTTP_400_BAD_REQUEST,
            "Cannot delete yourself",
            id="delete-self",
        ),
        pytest.param(
            "POST",
            f"/admin/users/{_USER_ID}/promote",
            None,
            "promote_superuser",
            ValueError(f"User {_USER_ID} is already a superuser"),
            status.HTTP_400_BAD_REQUEST,
            None,
            id="promote-already-superuser",
        ),
        pytest.param(
            "POST",
            f"/admin/users/{_USER_ID}/promote",
            None,
            "promote_superuser",
            ValueError(f"User {_USER_ID} not found"),
            status.HTTP_404_NOT_FOUND,
            None,
            id="promote-not-found",
        ),
        pytest.param(
            "POST",
            f"/admin/users/{_USER_ID}/demote",
            None,
            "demote_superuser",
            ValueError("Cannot demote yourself"),
            status.HTTP_400_BAD_REQUEST,
            "Cannot demote yourself",
            id="demote-self",
        ),
        pytest.param(
            "POST",
            f"/admin/users/{_USER_ID}/demote",
            None,
            "demote_superuser",
            ValueError("Cannot demote the last superuser"),
            status.HTTP_400_BAD_REQUEST,
            "Cannot demote the last superuser",
            id="demote-last-superuser",
        ),
        pytest.param(
            "POST",
            f"/admin/users/{_USER_ID}/demote",
            None,
            "demote_superuser",
            ValueError(f"User {_USER_ID} is not a superuser"),
            status.HTTP_400_BAD_REQUEST,
            None,
            id="demote-non-superuser",
        ),
        pytest.param(
            "POST",
            f"/admin/users/{_USER_ID}/demote",
            None,
            "demote_superuser",
            ValueError(f"User {_USER_ID} not found"),
            status.HTTP_404_NOT_FOUND,
            None,
            id="demote-not-found",
        ),
    ],
)
async def test_admin_user_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    payload: dict[str, str] | None,
    service_method: str,
    error: Exception,
    status_code: int,
    detail: str | None,
) -> None:
    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        getattr(mock_svc, service_method).side_effect = error
        MockService.return_value = mock_svc

        response = client.request(method, path, json=payload)

    assert response.status_code == status_code
    if detail is not None:
        assert detail in response.json()["detail"]


@pytest.mark.anyio
async def test_delete_user_success(client: TestClient, test_admin_role: Role) -> None:
    user_id = uuid.uuid4()

    with patch.object(users_router, "AdminUserService") as MockService:
        mock_svc = AsyncMock()
        MockService.return_value = mock_svc

        response = client.delete(f"/admin/users/{user_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_svc.delete_user.assert_awaited_once_with(
        user_id, current_user_id=test_admin_role.user_id
    )
