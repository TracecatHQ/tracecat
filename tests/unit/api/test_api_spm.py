"""HTTP-level tests for AI SPM API endpoints."""

import importlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.spm.schemas import (
    SpmControlRead,
    SpmEndpointCreateResponse,
    SpmEndpointRead,
    SpmEndpointSyncResponse,
    SpmEnforcementTaskRead,
)
from tracecat_ee.spm.types import (
    SpmAssetClass,
    SpmAssetType,
    SpmControlCheck,
    SpmEndpointPlatform,
    SpmEndpointStatus,
    SpmEnforcementAction,
    SpmEnforcementTaskStatus,
    SpmHarness,
    SpmSeverity,
)

from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired, TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse

spm_router_module = importlib.import_module("tracecat_ee.spm.router")


def _endpoint_read() -> SpmEndpointRead:
    now = datetime(2026, 4, 22, tzinfo=UTC)
    return SpmEndpointRead(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        organization_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        name="Chris MacBook",
        harness=SpmHarness.CLAUDE_CODE,
        platform=SpmEndpointPlatform.MACOS,
        status=SpmEndpointStatus.ACTIVE,
        hostname="chris-mbp",
        os_user="chris",
        home_path="/Users/chris",
        endpoint_version="0.1.0",
        client_metadata={"install_method": "manual"},
        enrolled_at=now,
        last_seen_at=now,
        last_sync_at=now,
        last_sync_error=None,
        created_at=now,
        updated_at=now,
    )


def _control_read() -> SpmControlRead:
    return SpmControlRead(
        id="claude.mcp_server.approved",
        revision="1",
        title="Claude MCP Server Must Be Approved",
        description="MCP servers configured for Claude must match an approved server-name plus resolved-identity tuple.",
        harness=SpmHarness.CLAUDE_CODE,
        asset_class=SpmAssetClass.MCP_SERVER,
        asset_type=SpmAssetType.MCP_SERVER,
        severity=SpmSeverity.HIGH,
        check=SpmControlCheck.MCP_SERVER_APPROVED,
        action=SpmEnforcementAction.DISABLE_MCP_SERVER,
    )


def _task_read() -> SpmEnforcementTaskRead:
    now = datetime(2026, 4, 22, tzinfo=UTC)
    return SpmEnforcementTaskRead(
        id=uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"),
        organization_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        endpoint_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        finding_id=uuid.UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc"),
        action=SpmEnforcementAction.DISABLE_MCP_SERVER,
        payload={
            "server_name": "github",
            "resolved_identity": "https://api.github.com",
        },
        status=SpmEnforcementTaskStatus.PENDING,
        requested_by_user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        completed_at=None,
        result={},
        error=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_list_spm_endpoints_requires_spm_entitlement(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(
        spm_router_module,
        "check_entitlement",
        new_callable=AsyncMock,
    ) as mock_check_entitlement:
        mock_check_entitlement.side_effect = EntitlementRequired("spm")

        response = client.get("/spm/endpoints")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_check_entitlement.assert_awaited_once()


@pytest.mark.anyio
async def test_list_spm_controls_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with (
        patch.object(
            spm_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ),
        patch.object(spm_router_module, "SpmService") as mock_service_cls,
    ):
        mock_service = AsyncMock()
        mock_service.list_controls.return_value = [_control_read()]
        mock_service_cls.return_value = mock_service

        response = client.get("/spm/controls")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["id"] == "claude.mcp_server.approved"


@pytest.mark.anyio
async def test_get_spm_control_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with (
        patch.object(
            spm_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ),
        patch.object(spm_router_module, "SpmService") as mock_service_cls,
    ):
        mock_service = AsyncMock()
        mock_service.get_control.return_value = _control_read()
        mock_service_cls.return_value = mock_service

        response = client.get("/spm/controls/claude.mcp_server.approved")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["check"] == "mcp_server_approved"


@pytest.mark.anyio
async def test_get_spm_control_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with (
        patch.object(
            spm_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ),
        patch.object(spm_router_module, "SpmService") as mock_service_cls,
    ):
        mock_service = AsyncMock()
        mock_service.get_control.side_effect = TracecatNotFoundError(
            "SPM control not found: missing"
        )
        mock_service_cls.return_value = mock_service

        response = client.get("/spm/controls/missing")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "SPM control not found: missing"


@pytest.mark.anyio
async def test_list_spm_endpoints_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with (
        patch.object(
            spm_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ),
        patch.object(spm_router_module, "SpmService") as mock_service_cls,
    ):
        mock_service = AsyncMock()
        mock_service.list_endpoints.return_value = CursorPaginatedResponse[
            SpmEndpointRead
        ](
            items=[_endpoint_read()],
            next_cursor="cursor-1",
            has_more=True,
        )
        mock_service_cls.return_value = mock_service

        response = client.get("/spm/endpoints")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["name"] == "Chris MacBook"
    assert response.json()["next_cursor"] == "cursor-1"


@pytest.mark.anyio
async def test_create_spm_endpoint_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with (
        patch.object(
            spm_router_module,
            "check_entitlement",
            new_callable=AsyncMock,
        ),
        patch.object(spm_router_module, "SpmService") as mock_service_cls,
    ):
        mock_service = AsyncMock()
        mock_service.create_endpoint.return_value = SpmEndpointCreateResponse(
            endpoint=_endpoint_read(),
            enrollment_token="tcspm_enroll_example",
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            "/spm/endpoints",
            json={
                "name": "Chris MacBook",
                "harness": "claude_code",
                "platform": "macos",
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["enrollment_token"] == "tcspm_enroll_example"


@pytest.mark.anyio
async def test_sync_spm_endpoint_requires_authorization_header(
    client: TestClient,
) -> None:
    response = client.post(
        "/spm/endpoints/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa/sync",
        json={},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_sync_spm_endpoint_passes_bearer_token(
    client: TestClient,
) -> None:
    with patch.object(spm_router_module, "SpmSyncService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.sync_endpoint.return_value = SpmEndpointSyncResponse(
            endpoint=_endpoint_read(),
            endpoint_secret="tcspm_ep_example",
            tasks=[_task_read()],
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            "/spm/endpoints/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa/sync",
            headers={"Authorization": "Bearer tcspm_enroll_example"},
            json={"assets": [], "task_results": []},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["endpoint_secret"] == "tcspm_ep_example"
    mock_service.sync_endpoint.assert_awaited_once()
    call_kwargs = mock_service.sync_endpoint.await_args.kwargs
    assert call_kwargs["bearer_token"] == "tcspm_enroll_example"
