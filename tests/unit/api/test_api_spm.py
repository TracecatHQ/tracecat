"""HTTP-level tests for AI SPM API endpoints."""

import importlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.spm.exceptions import SpmConflictError, SpmNotFoundError
from tracecat_ee.spm.schemas import (
    SpmAssetRead,
    SpmControlRead,
    SpmEndpointAssetRead,
    SpmEndpointCreateResponse,
    SpmEndpointRead,
    SpmEndpointSyncResponse,
    SpmEnforcementTaskRead,
    SpmFindingRead,
)
from tracecat_ee.spm.types import (
    SpmArtifactType,
    SpmAssetType,
    SpmEndpointPlatform,
    SpmEndpointStatus,
    SpmEnforcementAction,
    SpmEnforcementTaskStatus,
    SpmFindingStatus,
    SpmHarness,
    SpmSeverity,
)

from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired
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


def _asset_read() -> SpmAssetRead:
    now = datetime(2026, 4, 22, tzinfo=UTC)
    return SpmAssetRead(
        id=uuid.UUID("dddddddd-dddd-4ddd-dddd-dddddddddddd"),
        organization_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        harness=SpmHarness.CLAUDE_CODE,
        asset_type=SpmAssetType.MCP_SERVER,
        artifact_type=SpmArtifactType.CLAUDE_JSON,
        artifact_location="/Users/chris/.claude.json",
        identity_key="file:/Users/chris/.claude.json#mcp:github|https://api.github.com/mcp",
        display_name="github",
        content_hash="abc123",
        metadata={"file_path": "/Users/chris/.claude.json"},
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )


def _endpoint_asset_read() -> SpmEndpointAssetRead:
    now = datetime(2026, 4, 22, tzinfo=UTC)
    return SpmEndpointAssetRead(
        asset_id=uuid.UUID("dddddddd-dddd-4ddd-dddd-dddddddddddd"),
        asset_sighting_id=uuid.UUID("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee"),
        organization_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        endpoint_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        workspace_id=None,
        harness=SpmHarness.CLAUDE_CODE,
        asset_type=SpmAssetType.MCP_SERVER,
        artifact_type=SpmArtifactType.CLAUDE_JSON,
        artifact_location="/Users/chris/.claude.json",
        identity_key="file:/Users/chris/.claude.json#mcp:github|https://api.github.com/mcp",
        display_name="github",
        content_hash="abc123",
        metadata={"file_path": "/Users/chris/.claude.json"},
        evidence={"config": {"url": "https://api.github.com/mcp"}},
        observed_state={"disabled": False},
        first_seen_at=now,
        last_seen_at=now,
    )


def _control_read() -> SpmControlRead:
    return SpmControlRead(
        id=uuid.UUID("7dca8397-056a-4cc7-a4a6-3fef782b21a2"),
        key="claude.mcp_server.approved",
        aliases=[],
        revision="1",
        title="Claude MCP server must be approved",
        description="MCP servers configured for Claude must match an approved server-name plus resolved-identity tuple.",
        harness=SpmHarness.CLAUDE_CODE,
        asset_type=SpmAssetType.MCP_SERVER,
        severity=SpmSeverity.HIGH,
        action=SpmEnforcementAction.DISABLE_MCP_SERVER,
    )


def _finding_read() -> SpmFindingRead:
    now = datetime(2026, 4, 22, tzinfo=UTC)
    return SpmFindingRead(
        id=uuid.UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc"),
        organization_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        endpoint_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        asset_id=uuid.UUID("dddddddd-dddd-4ddd-dddd-dddddddddddd"),
        asset_sighting_id=uuid.UUID("eeeeeeee-eeee-4eee-eeee-eeeeeeeeeeee"),
        control_id=uuid.UUID("7dca8397-056a-4cc7-a4a6-3fef782b21a2"),
        control_key="claude.mcp_server.approved",
        control_revision="1",
        harness=SpmHarness.CLAUDE_CODE,
        asset_type=SpmAssetType.MCP_SERVER,
        artifact_type=SpmArtifactType.CLAUDE_JSON,
        artifact_location="/Users/chris/.claude.json",
        severity=SpmSeverity.HIGH,
        status=SpmFindingStatus.OPEN,
        summary="Github MCP server is not approved",
        evidence={"config": {"url": "https://api.github.com/mcp"}},
        enrichment={},
        recommended_action=SpmEnforcementAction.DISABLE_MCP_SERVER,
        recommended_payload={"server_name": "github"},
        opened_at=now,
        closed_at=None,
        last_decision_at=None,
        created_at=now,
        updated_at=now,
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
    assert response.json()["detail"]["code"] == "spm_entitlement_required"
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
    assert response.json()[0]["key"] == "claude.mcp_server.approved"


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
    assert response.json()["key"] == "claude.mcp_server.approved"


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
        mock_service.get_control.side_effect = SpmNotFoundError(
            "SPM control not found.",
            code="spm_control_not_found",
            control_id="missing",
        )
        mock_service_cls.return_value = mock_service

        response = client.get("/spm/controls/missing")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"]["code"] == "spm_control_not_found"


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
async def test_list_spm_endpoint_assets_success(
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
        mock_service.list_endpoint_assets.return_value = CursorPaginatedResponse[
            SpmEndpointAssetRead
        ](
            items=[_endpoint_asset_read()],
            next_cursor=None,
            has_more=False,
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            "/spm/endpoints/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa/assets"
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["display_name"] == "github"
    assert response.json()["items"][0]["observed_state"] == {"disabled": False}


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
async def test_delete_spm_endpoint_success(
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
        mock_service_cls.return_value = mock_service

        response = client.delete("/spm/endpoints/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_service.delete_pending_endpoint.assert_awaited_once_with(
        uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
    )


@pytest.mark.anyio
async def test_delete_spm_endpoint_rejects_non_pending_endpoint(
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
        mock_service.delete_pending_endpoint.side_effect = SpmConflictError(
            "Only pending enrollments that have never enrolled or synced can be removed.",
            code="spm_endpoint_delete_conflict",
            endpoint_id=uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
        )
        mock_service_cls.return_value = mock_service

        response = client.delete("/spm/endpoints/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "spm_endpoint_delete_conflict"


@pytest.mark.anyio
async def test_list_spm_assets_passes_filters(
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
        mock_service.list_assets.return_value = CursorPaginatedResponse[SpmAssetRead](
            items=[_asset_read()],
            next_cursor=None,
            has_more=False,
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            "/spm/assets",
            params={
                "endpoint_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                "harness": "claude_code",
                "asset_type": "mcp_server",
                "artifact_type": ".claude.json",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    params = mock_service.list_assets.await_args.args[0]
    assert str(params.endpoint_id) == "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
    assert params.harness == SpmHarness.CLAUDE_CODE
    assert params.asset_type == SpmAssetType.MCP_SERVER
    assert params.artifact_type == SpmArtifactType.CLAUDE_JSON


@pytest.mark.anyio
async def test_list_spm_findings_passes_filters(
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
        mock_service.list_findings.return_value = CursorPaginatedResponse[
            SpmFindingRead
        ](
            items=[_finding_read()],
            next_cursor=None,
            has_more=False,
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            "/spm/findings",
            params={
                "endpoint_id": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
                "control_id": "claude.mcp_server.approved",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    params = mock_service.list_findings.await_args.args[0]
    assert str(params.endpoint_id) == "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
    assert params.control_id == "claude.mcp_server.approved"


@pytest.mark.anyio
async def test_sync_spm_endpoint_requires_authorization_header(
    client: TestClient,
) -> None:
    response = client.post(
        "/spm/endpoints/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa/sync",
        json={},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"]["code"] == "spm_authorization_missing"


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
