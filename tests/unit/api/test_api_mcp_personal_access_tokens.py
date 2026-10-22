"""Router tests for MCP personal access token endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args, get_type_hints
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from tracecat.api.common import generic_exception_handler
from tracecat.auth.dependencies import WorkspaceUserPathRole
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.mcp.personal_access_tokens import router as mcp_pat_router
from tracecat.pagination import CursorPaginatedResponse


@pytest.mark.anyio
async def test_create_mcp_personal_access_token_rejects_nul_before_service(
    test_admin_role: Role,
) -> None:
    role = _with_workspace_read(test_admin_role)
    app = FastAPI()
    app.include_router(mcp_pat_router.router)
    app.add_exception_handler(Exception, generic_exception_handler)

    async def actor_role() -> Role:
        ctx_role.set(role)
        return role

    app.dependency_overrides[get_args(WorkspaceUserPathRole)[1].dependency] = actor_role
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    with patch.object(mcp_pat_router, "MCPPersonalAccessTokenService") as service:
        async with AsyncClient(
            transport=ASGITransport(app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/workspaces/{role.workspace_id}/mcp-personal-access-tokens",
                json={"name": "synthetic\x00name"},
            )
    assert response.status_code == 422
    service.assert_not_called()


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
