"""HTTP-level tests for registry actions endpoints."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.auth.credentials as auth_credentials_module
import tracecat.registry.actions.router as registry_actions_router
from tracecat.auth.types import Role
from tracecat.registry.actions.types import IndexEntry


@pytest.fixture
def mock_role_acl_dependency(
    test_admin_role: Role,
) -> Generator[AsyncMock, None, None]:
    """Bypass RoleACL auth for registry action route tests."""
    with patch.object(
        auth_credentials_module, "_role_dependency", new_callable=AsyncMock
    ) as mock_role_dependency:
        mock_role_dependency.return_value = test_admin_role
        yield mock_role_dependency


@pytest.mark.anyio
async def test_list_registry_actions_include_locked_returns_availability_metadata(
    client: TestClient,
    test_admin_role: Role,
    mock_role_acl_dependency: AsyncMock,
) -> None:
    """Test GET /registry/actions supports include_locked."""

    with patch.object(
        registry_actions_router, "RegistryActionsService"
    ) as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.list_actions_from_index.return_value = [
            (
                IndexEntry(
                    id=uuid4(),
                    namespace="core.cases",
                    name="create_task",
                    action_type="udf",
                    description="Create a task",
                    default_title="Create task",
                    display_group="Cases",
                    options={"required_entitlements": ["case_addons"]},
                    missing_entitlements=("case_addons",),
                ),
                "tracecat_registry",
            )
        ]
        mock_service_cls.return_value = mock_service

        response = client.get("/registry/actions", params={"include_locked": "true"})

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload[0]["availability"] == {
        "locked": True,
        "missing_entitlements": ["case_addons"],
    }
    mock_service.list_actions_from_index.assert_awaited_once_with(include_locked=True)
