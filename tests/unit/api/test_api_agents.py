"""HTTP-level tests for agent management API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent import router as agent_router
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method", "error", "status_code"),
    [
        pytest.param(
            "GET",
            "/agent/providers/invalid/config",
            None,
            "get_provider_credential_config",
            TracecatNotFoundError("Provider not found"),
            status.HTTP_404_NOT_FOUND,
            id="get-provider-config-not-found",
        ),
        pytest.param(
            "POST",
            "/agent/credentials",
            {"provider": "openai", "credentials": {"api_key": "invalid"}},
            "create_provider_credentials",
            Exception("Invalid credentials"),
            status.HTTP_400_BAD_REQUEST,
            id="create-credentials-error",
        ),
        pytest.param(
            "PUT",
            "/agent/credentials/invalid",
            {"credentials": {"api_key": "test"}},
            "update_provider_credentials",
            TracecatNotFoundError("Credentials not found"),
            status.HTTP_404_NOT_FOUND,
            id="update-credentials-not-found",
        ),
        pytest.param(
            "PUT",
            "/agent/default-model",
            {"catalog_id": str(uuid.uuid4())},
            "set_default_model",
            TracecatNotFoundError("Model not found"),
            status.HTTP_404_NOT_FOUND,
            id="set-default-model-not-found",
        ),
    ],
)
async def test_agent_management_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    service_method: str,
    error: Exception,
    status_code: int,
) -> None:
    with patch.object(agent_router, "AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        getattr(mock_svc, service_method).side_effect = error
        MockService.return_value = mock_svc

        response = client.request(method, path, json=payload)

    assert response.status_code == status_code


@pytest.mark.anyio
async def test_get_default_model_none(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /agent/default-model when no default is set."""
    with patch.object(agent_router, "AgentManagementService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_default_model.return_value = None
        MockService.return_value = mock_svc

        response = client.get("/agent/default-model")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() is None
