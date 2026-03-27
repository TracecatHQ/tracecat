"""Targeted HTTP-level tests for agent API behavior."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.catalog import router as catalog_router
from tracecat.agent.credentials import router as credentials_router
from tracecat.agent.schemas import ModelSelection, WorkspaceModelSubsetRead
from tracecat.agent.selections import router as selections_router
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
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
    with patch.object(
        credentials_router, "AgentCredentialsService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_provider_credential_config.side_effect = TracecatNotFoundError(
            "Provider not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers/invalid/config")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_providers_status_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(
        credentials_router, "AgentCredentialsService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_providers_status.return_value = {
            "openai": True,
            "anthropic": False,
        }
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"openai": True, "anthropic": False}


@pytest.mark.anyio
async def test_list_providers_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(catalog_router, "AgentCatalogService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_providers.return_value = []
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/providers")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
    mock_svc.list_providers.assert_awaited_once_with(
        configured_only=True,
        include_discovered_models=False,
    )


@pytest.mark.anyio
async def test_list_providers_accepts_explicit_query_flags(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(catalog_router, "AgentCatalogService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_providers.return_value = []
        mock_service_cls.return_value = mock_svc

        response = client.get(
            "/agent/providers",
            params={
                "configured_only": "false",
                "include_discovered_models": "true",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
    mock_svc.list_providers.assert_awaited_once_with(
        configured_only=False,
        include_discovered_models=True,
    )


@pytest.mark.anyio
async def test_create_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(
        credentials_router, "AgentCredentialsService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.create_provider_credentials.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.post(
            "/agent/credentials",
            json={
                "provider": "openai",
                "credentials": {"OPENAI_API_KEY": "sk-test"},
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json() == {"message": "Credentials for openai created successfully"}
    mock_svc.create_provider_credentials.assert_awaited_once()


@pytest.mark.anyio
async def test_update_provider_credentials_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(
        credentials_router, "AgentCredentialsService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.update_provider_credentials.side_effect = TracecatNotFoundError(
            "missing"
        )
        mock_service_cls.return_value = mock_svc

        response = client.put(
            "/agent/credentials/openai",
            json={"credentials": {"OPENAI_API_KEY": "sk-test"}},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Credentials for provider openai not found"}


@pytest.mark.anyio
async def test_delete_provider_credentials_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(
        credentials_router, "AgentCredentialsService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.delete_provider_credentials.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.delete("/agent/credentials/openai")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "Credentials for openai deleted successfully"}


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
    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
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
    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
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

    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
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

    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
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

    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
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

    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
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

    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
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

    with patch.object(selections_router, "AgentSelectionsService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.clear_workspace_model_subset.side_effect = TracecatNotFoundError(
            f"Workspace {workspace_id} not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.delete(f"/agent/workspaces/{workspace_id}/model-subset")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_list_models_allows_workspace_filter_with_workspace_access(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None
    restricted_role = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                scope
                for scope in (test_admin_role.scopes or frozenset())
                if scope != "org:workspace:read"
            )
        }
    )
    token = ctx_role.set(restricted_role)
    try:
        with patch.object(
            selections_router, "AgentSelectionsService"
        ) as mock_service_cls:
            mock_svc = AsyncMock()
            mock_svc.list_models.return_value = []
            mock_service_cls.return_value = mock_svc

            response = client.get(f"/agent/models?workspace_id={workspace_id}")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    mock_service_cls.assert_called_once()


@pytest.mark.anyio
async def test_list_models_rejects_workspace_filter_outside_role_workspace(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = uuid.uuid4()
    token = ctx_role.set(test_admin_role)
    try:
        with patch.object(
            selections_router, "AgentSelectionsService"
        ) as mock_service_cls:
            response = client.get(f"/agent/models?workspace_id={workspace_id}")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_service_cls.assert_not_called()


@pytest.mark.anyio
async def test_list_platform_catalog_invalid_cursor_returns_bad_request(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(catalog_router, "AgentCatalogService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_builtin_catalog.side_effect = ValueError(
            "Invalid cursor. Expected a non-negative integer offset."
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/catalog/platform?cursor=abc")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Invalid cursor. Expected a non-negative integer offset."
    }


@pytest.mark.anyio
async def test_get_workspace_model_subset_allows_workspace_member_without_org_workspace_scope(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None
    restricted_role = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                scope
                for scope in (test_admin_role.scopes or frozenset())
                if scope != "org:workspace:read"
            )
        }
    )
    token = ctx_role.set(restricted_role)
    try:
        with patch.object(
            selections_router, "AgentSelectionsService"
        ) as mock_service_cls:
            mock_svc = AsyncMock()
            mock_svc.get_workspace_model_subset.return_value = WorkspaceModelSubsetRead(
                inherit_all=True,
                models=[],
            )
            mock_service_cls.return_value = mock_svc

            response = client.get(f"/agent/workspaces/{workspace_id}/model-subset")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
