"""HTTP-level tests for organization members API endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
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
    mock_session = await app.dependency_overrides[get_async_session]()

    # Mock the RBAC role lookup query result
    rbac_tuples = Mock()
    rbac_tuples.all.return_value = [(user.id, "Admin", "organization-admin")]
    rbac_result = Mock()
    rbac_result.tuples.return_value = rbac_tuples

    # Mock the agent preset query result
    agent_result = Mock()
    agent_result.all.return_value = []

    mock_session.execute = AsyncMock(side_effect=[rbac_result, agent_result])

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [user]
        mock_svc.list_invitations.return_value = []
        MockService.return_value = mock_svc

        response = client.get("/organization/members")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == str(user.id)
    assert "is_superuser" not in data[0]


@pytest.mark.anyio
async def test_list_org_members_includes_agent_presets(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()
    preset_id = uuid.uuid4()
    mock_session = await app.dependency_overrides[get_async_session]()

    rbac_tuples = Mock()
    rbac_tuples.all.return_value = [(user.id, "Admin", "organization-admin")]
    rbac_result = Mock()
    rbac_result.tuples.return_value = rbac_tuples

    agent_result = Mock()
    agent_result.all.return_value = [
        (
            preset_id,
            "General assistant",
            "Primary workspace",
            "Preset Runner",
        )
    ]

    mock_session.execute = AsyncMock(side_effect=[rbac_result, agent_result])

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [user]
        mock_svc.list_invitations.return_value = []
        MockService.return_value = mock_svc

        response = client.get("/organization/members")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2
    agent_member = next(
        item for item in data if item.get("role_slug") == f"agent-preset:{preset_id}"
    )
    assert agent_member["user_id"] == str(preset_id)
    assert agent_member["email"] == f"preset-{preset_id.hex}@agent-presets.example.com"
    assert agent_member["role_name"] == "Preset Runner"
    assert agent_member["first_name"] == "Agent preset"


@pytest.mark.anyio
async def test_update_org_member_omits_superuser_flag(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()
    mock_session = await app.dependency_overrides[get_async_session]()

    # Mock the RBAC role name query result
    rbac_result = Mock()
    rbac_result.scalar_one_or_none.return_value = "Admin"
    mock_session.execute = AsyncMock(return_value=rbac_result)

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_member.return_value = user
        MockService.return_value = mock_svc

        response = client.patch(
            f"/organization/members/{user.id}",
            json={"role": "basic"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["user_id"] == str(user.id)
    assert data["role"] == "Admin"
    assert "is_superuser" not in data


@pytest.mark.anyio
async def test_delete_organization_requires_owner_role(
    client: TestClient, test_admin_role: Role
) -> None:
    response = client.delete("/organization?confirm=Test%20Organization")
    assert response.status_code == status.HTTP_403_FORBIDDEN
