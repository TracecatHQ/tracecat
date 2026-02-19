"""HTTP-level tests for organization members API endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.authz.enums import OrgRole
from tracecat.authz.scopes import ORG_OWNER_SCOPES
from tracecat.contexts import ctx_role
from tracecat.exceptions import TracecatValidationError
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


def _override_role_dependency() -> Role:
    role = ctx_role.get()
    if role is None:
        raise RuntimeError("No role set in ctx_role context")
    return role


@pytest.fixture(autouse=True)
def _override_organization_role_dependencies(  # pyright: ignore[reportUnusedFunction]
    client: TestClient,
):
    role_dependencies = [
        organization_router.OrgUserRole,
    ]

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides[dependency] = _override_role_dependency

    yield

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides.pop(dependency, None)


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


@pytest.mark.anyio
async def test_delete_organization_requires_owner_role(
    client: TestClient, test_admin_role: Role
) -> None:
    response = client.delete("/organization?confirm=Test%20Organization")
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_delete_organization_owner_success(
    client: TestClient, test_admin_role: Role
) -> None:
    owner_role = test_admin_role.model_copy(
        update={"org_role": OrgRole.OWNER, "scopes": ORG_OWNER_SCOPES}
    )
    token = ctx_role.set(owner_role)
    try:
        with patch.object(organization_router, "OrgService") as MockService:
            mock_svc = AsyncMock()
            mock_svc.delete_organization.return_value = None
            MockService.return_value = mock_svc

            response = client.delete("/organization?confirm=Test%20Organization")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_svc.delete_organization.assert_awaited_once_with(
        confirmation="Test Organization"
    )


@pytest.mark.anyio
async def test_delete_organization_bad_confirmation_returns_400(
    client: TestClient, test_admin_role: Role
) -> None:
    owner_role = test_admin_role.model_copy(
        update={"org_role": OrgRole.OWNER, "scopes": ORG_OWNER_SCOPES}
    )
    token = ctx_role.set(owner_role)
    try:
        with patch.object(organization_router, "OrgService") as MockService:
            mock_svc = AsyncMock()
            mock_svc.delete_organization.side_effect = TracecatValidationError(
                "Confirmation text must exactly match the organization name."
            )
            MockService.return_value = mock_svc

            response = client.delete("/organization?confirm=wrong")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
