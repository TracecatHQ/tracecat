import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tests._route_utils import iter_effective_api_routes
from tracecat.api.app import app
from tracecat.auth.enums import CredentialChangeErrorCode
from tracecat.auth.router import current_active_user_token
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.auth.users import (
    CredentialChangeError,
    current_active_user,
    get_user_manager,
)
from tracecat.db.models import User


def test_fastapi_users_routes_set_authenticated_user_context() -> None:
    routes = [
        route
        for route in iter_effective_api_routes(app)
        if route.name.startswith("users:")
    ]

    route_names = {route.name for route in routes}
    assert route_names == {
        "users:current_user",
        "users:patch_current_user",
        "users:user",
        "users:patch_user",
        "users:delete_user",
    }

    for route in routes:
        dependency_names = {
            getattr(dependency.call, "__name__", None)
            for dependency in route.dependant.dependencies
        }
        assert "authenticated_user_only" in dependency_names


def _override_user_and_manager(
    user_manager: AsyncMock,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email="user@example.com",
        hashed_password="hashed",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        role=UserRole.BASIC,
        settings={},
    )
    app.dependency_overrides[current_active_user] = lambda: user
    app.dependency_overrides[current_active_user_token] = lambda: (user, "current")
    app.dependency_overrides[get_user_manager] = lambda: user_manager
    return user


@pytest.mark.anyio
async def test_patch_current_user_rejects_credential_fields(
    client: TestClient, test_admin_role: Role
) -> None:
    user_manager = AsyncMock()
    _override_user_and_manager(user_manager)

    for field, value in (("email", "other@example.com"), ("password", "x" * 16)):
        response = client.patch("/users/me", json={field: value})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    user_manager.update.assert_not_awaited()


@pytest.mark.anyio
async def test_patch_current_user_updates_profile_fields(
    client: TestClient, test_admin_role: Role
) -> None:
    user_manager = AsyncMock()
    user = _override_user_and_manager(user_manager)
    user_manager.update.return_value = user

    response = client.patch("/users/me", json={"first_name": "Ada"})

    assert response.status_code == status.HTTP_200_OK
    update = user_manager.update.await_args.args[0]
    assert update.model_dump(exclude_unset=True) == {"first_name": "Ada"}


@pytest.mark.anyio
async def test_change_password_route_returns_error_code(
    client: TestClient, test_admin_role: Role
) -> None:
    user_manager = AsyncMock()
    _override_user_and_manager(user_manager)
    user_manager.change_password.side_effect = CredentialChangeError(
        CredentialChangeErrorCode.INVALID_CURRENT_PASSWORD
    )

    response = client.post(
        "/users/me/password",
        json={"current_password": "wrong", "new_password": "new-password-1234"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"]["code"] == "invalid_current_password"
    assert user_manager.change_password.await_args.kwargs["current_token"] == "current"


@pytest.mark.anyio
async def test_change_email_route_forbidden_without_password_login(
    client: TestClient, test_admin_role: Role
) -> None:
    user_manager = AsyncMock()
    _override_user_and_manager(user_manager)
    user_manager.change_email.side_effect = CredentialChangeError(
        CredentialChangeErrorCode.PASSWORD_LOGIN_NOT_ALLOWED
    )

    response = client.post(
        "/users/me/email",
        json={"current_password": "password", "new_email": "new@example.com"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"]["code"] == "password_login_not_allowed"
