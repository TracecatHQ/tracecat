"""HTTP-level tests for credential sync API endpoints."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.credential_sync import router as credential_sync_router


@pytest.fixture
async def credential_sync_role() -> AsyncGenerator[Role, None]:
    role = Role(
        type="user",
        user_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"org:credential-sync:manage"}),
    )
    token = ctx_role.set(role)
    try:
        yield role
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_get_aws_config_success(
    client: TestClient,
    credential_sync_role: Role,
) -> None:
    with patch.object(credential_sync_router, "CredentialSyncService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_aws_config.return_value = {
            "region": "us-east-1",
            "secret_prefix": "tracecat/test-sync",
            "has_access_key_id": True,
            "has_secret_access_key": True,
            "has_session_token": False,
            "is_configured": True,
            "is_corrupted": False,
        }
        MockService.return_value = mock_svc

        response = client.get("/organization/credentials/sync/aws")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["secret_prefix"] == "tracecat/test-sync"


@pytest.mark.anyio
async def test_update_aws_config_success(
    client: TestClient,
    credential_sync_role: Role,
) -> None:
    with patch.object(credential_sync_router, "CredentialSyncService") as MockService:
        mock_svc = AsyncMock()
        MockService.return_value = mock_svc

        response = client.patch(
            "/organization/credentials/sync/aws",
            json={
                "region": "us-east-1",
                "secret_prefix": "tracecat/test-sync",
                "access_key_id": "AKIA_TEST",
                "secret_access_key": "secret-test-key",
            },
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_update_aws_config_validation_error(
    client: TestClient,
    credential_sync_role: Role,
) -> None:
    with patch.object(credential_sync_router, "CredentialSyncService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_aws_config.side_effect = ValueError("invalid config")
        MockService.return_value = mock_svc

        response = client.patch(
            "/organization/credentials/sync/aws",
            json={"region": "us-east-1"},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.json()["detail"] == "invalid config"


@pytest.mark.anyio
async def test_push_aws_credentials_success(
    client: TestClient,
    credential_sync_role: Role,
) -> None:
    with patch.object(credential_sync_router, "CredentialSyncService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.push_aws_credentials.return_value = {
            "provider": "aws",
            "operation": "push",
            "success": True,
            "processed": 1,
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
        }
        MockService.return_value = mock_svc

        response = client.post(
            f"/workspaces/{credential_sync_role.workspace_id}/credentials/sync/aws/push"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["operation"] == "push"


@pytest.mark.anyio
async def test_pull_aws_credentials_bad_request(
    client: TestClient,
    credential_sync_role: Role,
) -> None:
    with patch.object(credential_sync_router, "CredentialSyncService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.pull_aws_credentials.side_effect = ValueError("not configured")
        MockService.return_value = mock_svc

        response = client.post(
            f"/workspaces/{credential_sync_role.workspace_id}/credentials/sync/aws/pull"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "not configured"
