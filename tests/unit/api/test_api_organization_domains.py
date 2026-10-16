"""HTTP-level tests for organization domains endpoints."""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role


@pytest.mark.anyio
async def test_list_organization_domains_requires_org_context(
    client: TestClient, test_admin_role: Role
) -> None:
    role_without_org = test_admin_role.model_copy(update={"organization_id": None})
    token = ctx_role.set(role_without_org)
    try:
        response = client.get("/organization/domains")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "No organization context"
