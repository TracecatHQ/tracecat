"""Router tests for MCP personal access token endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.routing import APIRoute

from tracecat.auth.dependencies import WorkspaceUserPathRole
from tracecat.auth.types import Role
from tracecat.mcp.personal_access_tokens import router as mcp_pat_router
from tracecat.mcp.personal_access_tokens.schemas import MCPPersonalAccessTokenCreate
from tracecat.pagination import CursorPaginatedResponse


def _token_read(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID,
):
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        name="Claude Desktop",
        key_id="mcp_key_123",
        preview="tc_mcp_pat_...abcd",
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        created_by=user_id,
        revoked_by=None,
        created_at=now,
        updated_at=now,
    )


def _with_workspace_read(role: Role) -> Role:
    return role.model_copy(
        update={
            "scopes": frozenset(set(role.scopes or frozenset()) | {"workspace:read"})
        }
    )


@pytest.mark.anyio
async def test_list_mcp_personal_access_tokens_success(
    test_admin_role: Role,
) -> None:
    role = _with_workspace_read(test_admin_role)
    organization_id = role.organization_id
    user_id = role.user_id
    workspace_id = role.workspace_id
    assert organization_id is not None
    assert user_id is not None
    assert workspace_id is not None
    token_read = _token_read(organization_id, user_id, workspace_id=workspace_id)
    page = CursorPaginatedResponse(
        items=[token_read],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=1,
    )

    with patch.object(
        mcp_pat_router, "MCPPersonalAccessTokenService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_tokens.return_value = page
        mock_service_cls.return_value = mock_svc

        response = await mcp_pat_router.list_mcp_personal_access_tokens(
            role=role,
            session=AsyncMock(),
            limit=20,
            cursor=None,
            reverse=False,
        )

    payload = response.model_dump(mode="json")
    assert payload["items"][0]["id"] == str(token_read.id)
    assert payload["items"][0]["organization_id"] == str(organization_id)
    assert payload["items"][0]["preview"] == "tc_mcp_pat_...abcd"
    assert "raw_token" not in payload["items"][0]


@pytest.mark.anyio
async def test_create_mcp_personal_access_token_success(
    test_admin_role: Role,
) -> None:
    role = _with_workspace_read(test_admin_role)
    organization_id = role.organization_id
    user_id = role.user_id
    workspace_id = role.workspace_id
    assert organization_id is not None
    assert user_id is not None
    assert workspace_id is not None
    token_read = _token_read(organization_id, user_id, workspace_id=workspace_id)

    with patch.object(
        mcp_pat_router, "MCPPersonalAccessTokenService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.create_token.return_value = (token_read, "tc_mcp_pat_raw-secret")
        mock_service_cls.return_value = mock_svc

        response = await mcp_pat_router.create_mcp_personal_access_token(
            role=role,
            session=AsyncMock(),
            params=MCPPersonalAccessTokenCreate(
                name="Claude Desktop",
            ),
        )

    payload = response.model_dump(mode="json")
    assert payload["issued_token"]["raw_token"] == "tc_mcp_pat_raw-secret"
    assert payload["issued_token"]["token"]["id"] == str(token_read.id)
    mock_svc.create_token.assert_awaited_once_with(
        name="Claude Desktop",
        expires_at=None,
    )


@pytest.mark.anyio
async def test_revoke_mcp_personal_access_token_success(
    test_admin_role: Role,
) -> None:
    role = _with_workspace_read(test_admin_role)
    token_id = uuid.uuid4()

    with patch.object(
        mcp_pat_router, "MCPPersonalAccessTokenService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_service_cls.return_value = mock_svc

        response = await mcp_pat_router.revoke_mcp_personal_access_token(
            role=role,
            session=AsyncMock(),
            token_id=token_id,
        )

    assert response is None
    mock_svc.revoke_token.assert_awaited_once_with(token_id)


def test_mcp_personal_access_token_routes_are_workspace_scoped() -> None:
    routes = [
        route for route in mcp_pat_router.router.routes if isinstance(route, APIRoute)
    ]
    route_paths = {route.path for route in routes}

    assert route_paths == {
        "/workspaces/{workspace_id}/mcp-personal-access-tokens",
        "/workspaces/{workspace_id}/mcp-personal-access-tokens/{token_id}/revoke",
    }
    assert all("/organization/" not in route.path for route in routes)


def test_mcp_personal_access_token_routes_remain_workspace_user_only() -> None:
    endpoints = [
        mcp_pat_router.list_mcp_personal_access_tokens,
        mcp_pat_router.create_mcp_personal_access_token,
        mcp_pat_router.revoke_mcp_personal_access_token,
    ]

    for endpoint in endpoints:
        role = get_type_hints(endpoint, include_extras=True)["role"]
        assert role == WorkspaceUserPathRole
