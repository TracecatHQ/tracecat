"""HTTP-level tests for workspace entitlement errors."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES, ORG_ADMIN_SCOPES
from tracecat.contexts import ctx_role
from tracecat.exceptions import EntitlementRequired
from tracecat.tiers.enums import Entitlement
from tracecat.workspaces import router as workspaces_router


@pytest.fixture
def test_admin_role() -> Role:
    """Supply route authorization without creating an unrelated workspace."""
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES | ORG_ADMIN_SCOPES,
    )


@pytest.mark.anyio
async def test_create_workspace_entitlement_error_has_structured_http_response(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """The API exposes a workspace entitlement denial as a structured 403."""
    with patch.object(workspaces_router, "WorkspaceService") as mock_service_class:
        mock_service = AsyncMock()
        mock_service.create_workspace.side_effect = EntitlementRequired(
            Entitlement.MULTI_WORKSPACE.value
        )
        mock_service_class.return_value = mock_service

        token = ctx_role.set(test_admin_role)
        try:
            response = client.post("/workspaces", json={"name": "additional"})
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == Entitlement.MULTI_WORKSPACE.value
