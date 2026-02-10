"""HTTP-level tests for organization domains endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session


@pytest.mark.anyio
async def test_list_organization_domains_success(
    client: TestClient, test_admin_role: Role
) -> None:
    mock_session = await app.dependency_overrides[get_async_session]()

    now = datetime.now(UTC)
    organization_id = test_admin_role.organization_id
    assert organization_id is not None
    domain_id = uuid.uuid4()

    mock_domain = SimpleNamespace(
        id=domain_id,
        organization_id=organization_id,
        domain="acme.com",
        normalized_domain="acme.com",
        is_primary=True,
        is_active=True,
        verified_at=None,
        verification_method="platform_admin",
        created_at=now,
        updated_at=now,
    )

    scalar_result = Mock()
    scalar_result.all.return_value = [mock_domain]
    query_result = Mock()
    query_result.scalars.return_value = scalar_result

    mock_session.execute.side_effect = [query_result]

    response = client.get("/organization/domains")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == str(domain_id)
    assert payload[0]["organization_id"] == str(organization_id)
    assert payload[0]["domain"] == "acme.com"
    assert payload[0]["normalized_domain"] == "acme.com"
    assert payload[0]["is_primary"] is True
    assert payload[0]["is_active"] is True
    assert payload[0]["verification_method"] == "platform_admin"


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
