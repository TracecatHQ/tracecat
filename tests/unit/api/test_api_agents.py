"""Targeted HTTP-level tests for agent API behavior."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent import router as agent_router
from tracecat.agent.schemas import ModelSelection, WorkspaceModelSubsetRead
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError


@pytest.mark.anyio
async def test_workspace_provider_status_route_is_removed(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    response = client.get("/agent/workspace/providers/status")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_provider_credential_config_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_provider_credential_config.side_effect = TracecatNotFoundError(
            "Provider not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers/invalid/config")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_provider_refresh_route_is_removed(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    response = client.post("/agent/providers/openai/refresh")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_set_default_model_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.set_default_model_selection.side_effect = TracecatNotFoundError(
            "Model not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.put(
            "/agent/default-model",
            json={
                "source_id": None,
                "model_provider": "openai",
                "model_name": "gpt-5.2",
            },
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_disable_models_uses_post_batch_endpoint(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_service_cls.return_value = mock_svc

        response = client.post(
            "/agent/models/disabled/batch",
            json={
                "models": [
                    {
                        "source_id": None,
                        "model_provider": "openai",
                        "model_name": "gpt-5.2",
                    }
                ]
            },
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_svc.disable_models.assert_awaited_once()


@pytest.mark.anyio
async def test_get_workspace_model_subset_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None

    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_workspace_model_subset.return_value = WorkspaceModelSubsetRead(
            inherit_all=False,
            models=[
                ModelSelection(
                    source_id=None,
                    model_provider="openai",
                    model_name="gpt-5.2",
                )
            ],
        )
        mock_service_cls.return_value = mock_svc

        response = client.get(f"/agent/workspaces/{workspace_id}/model-subset")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["inherit_all"] is False
    assert body["models"] == [
        {
            "source_id": None,
            "model_provider": "openai",
            "model_name": "gpt-5.2",
        }
    ]


@pytest.mark.anyio
async def test_replace_workspace_model_subset_rejects_explicit_empty(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None

    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.replace_workspace_model_subset.side_effect = ValueError(
            "Workspace subsets must include at least one model when inherit_all is false."
        )
        mock_service_cls.return_value = mock_svc

        response = client.put(
            f"/agent/workspaces/{workspace_id}/model-subset",
            json={"inherit_all": False, "models": []},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Workspace subsets must include at least one model when inherit_all is false."
    }


@pytest.mark.anyio
async def test_replace_workspace_model_subset_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = uuid.uuid4()

    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.replace_workspace_model_subset.side_effect = TracecatNotFoundError(
            f"Workspace {workspace_id} not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.put(
            f"/agent/workspaces/{workspace_id}/model-subset",
            json={
                "inherit_all": False,
                "models": [
                    {
                        "source_id": None,
                        "model_provider": "openai",
                        "model_name": "gpt-5.2",
                    }
                ],
            },
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": f"Workspace {workspace_id} not found"}


@pytest.mark.anyio
async def test_replace_workspace_model_subset_bad_request(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None

    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.replace_workspace_model_subset.side_effect = ValueError(
            "Model openai/gpt-5.2 is not enabled for the organization"
        )
        mock_service_cls.return_value = mock_svc

        response = client.put(
            f"/agent/workspaces/{workspace_id}/model-subset",
            json={
                "inherit_all": False,
                "models": [
                    {
                        "source_id": None,
                        "model_provider": "openai",
                        "model_name": "gpt-5.2",
                    }
                ],
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Model openai/gpt-5.2 is not enabled for the organization"
    }


@pytest.mark.anyio
async def test_clear_workspace_model_subset_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None

    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.clear_workspace_model_subset.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.delete(f"/agent/workspaces/{workspace_id}/model-subset")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""


@pytest.mark.anyio
async def test_clear_workspace_model_subset_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = uuid.uuid4()

    with patch.object(agent_router, "AgentManagementService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.clear_workspace_model_subset.side_effect = TracecatNotFoundError(
            f"Workspace {workspace_id} not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.delete(f"/agent/workspaces/{workspace_id}/model-subset")

    assert response.status_code == status.HTTP_404_NOT_FOUND
