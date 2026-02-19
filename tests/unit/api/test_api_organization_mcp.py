"""HTTP-level tests for organization MCP connect endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.organization import router as organization_router


def _override_role_dependency() -> Role:
    role = ctx_role.get()
    if role is None:
        raise RuntimeError("No role set in ctx_role context")
    return role


@pytest.fixture(autouse=True)
def _override_organization_role_dependencies(  # pyright: ignore[reportUnusedFunction]
    client: TestClient,
):
    role_dependencies = [organization_router.OrgUserRole]

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides[dependency] = _override_role_dependency

    app.dependency_overrides[organization_router.current_active_user] = lambda: (
        SimpleNamespace(email="alice@example.com")
    )

    yield

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides.pop(dependency, None)
    app.dependency_overrides.pop(organization_router.current_active_user, None)


@pytest.mark.anyio
async def test_get_org_mcp_connect(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    org_id = test_admin_role.organization_id
    assert org_id is not None
    expires_at = datetime.now(UTC)

    with (
        patch.object(
            organization_router,
            "get_mcp_server_url",
            return_value="https://mcp.example.com/mcp",
        ),
        patch.object(
            organization_router,
            "mint_mcp_connection_scope_token",
            return_value=("scope-token", expires_at),
        ),
        patch.object(
            organization_router,
            "build_scoped_mcp_server_url",
            return_value="https://mcp.example.com/mcp?scope=scope-token",
        ),
    ):
        response = client.get("/organization/mcp/connect")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["organization_id"] == str(org_id)
    assert payload["server_url"] == "https://mcp.example.com/mcp"
    assert (
        payload["scoped_server_url"] == "https://mcp.example.com/mcp?scope=scope-token"
    )
    assert "codex" in payload["snippets"]
    assert "claude_code" in payload["snippets"]
    assert "cursor" in payload["snippets"]


@pytest.mark.anyio
async def test_get_org_mcp_connect_service_unavailable(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(
        organization_router,
        "mint_mcp_connection_scope_token",
        side_effect=ValueError("TRACECAT_MCP__BASE_URL must be configured"),
    ):
        response = client.get("/organization/mcp/connect")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    payload = response.json()
    assert payload["detail"] == "MCP connect is temporarily unavailable"
    assert "TRACECAT_MCP__BASE_URL" not in payload["detail"]
