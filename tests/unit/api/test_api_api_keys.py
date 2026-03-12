"""HTTP-level tests for API key endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api_keys import router as api_keys_router
from tracecat.api_keys.schemas import ApiKeyScopeRead, OrganizationApiKeyRead
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.pagination import CursorPaginatedResponse


def _scope_read(name: str, *, action: str = "read") -> ApiKeyScopeRead:
    resource = name.rsplit(":", maxsplit=1)[0]
    return ApiKeyScopeRead(
        id=uuid.uuid4(),
        name=name,
        resource=resource,
        action=action,
        description=f"{name} description",
    )


def _organization_api_key_read(organization_id: uuid.UUID) -> OrganizationApiKeyRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return OrganizationApiKeyRead(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Org automation",
        description="test key",
        key_id="key_123",
        preview="tc_org_sk_...abcd",
        created_by=None,
        revoked_by=None,
        last_used_at=None,
        revoked_at=None,
        created_at=now,
        updated_at=now,
        scopes=[_scope_read("workflow:read")],
    )


def _strip_org_api_key_read_access(scopes: frozenset[str] | None) -> frozenset[str]:
    return frozenset(
        scope
        for scope in (scopes or frozenset())
        if scope not in {"org:api_key:read", "org:api_key:update"}
    )


@pytest.mark.anyio
async def test_list_organization_api_keys_requires_read_scope(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_without_read = test_admin_role.model_copy(
        update={"scopes": _strip_org_api_key_read_access(test_admin_role.scopes)}
    )
    token = ctx_role.set(role_without_read)
    try:
        response = client.get("/organization/api-keys")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_list_organization_api_key_scopes_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset()) | {"org:api_key:read"}
            ),
        }
    )
    scope = _scope_read("workflow:read")

    with patch.object(api_keys_router, "OrganizationApiKeyService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_assignable_scopes.return_value = [scope]
        MockService.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get("/organization/api-keys/scopes")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "items": [
            {
                "id": str(scope.id),
                "name": "workflow:read",
                "resource": "workflow",
                "action": "read",
                "description": "workflow:read description",
            }
        ]
    }


@pytest.mark.anyio
async def test_get_organization_api_key_requires_read_scope(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_without_read = test_admin_role.model_copy(
        update={"scopes": _strip_org_api_key_read_access(test_admin_role.scopes)}
    )
    token = ctx_role.set(role_without_read)
    try:
        response = client.get(f"/organization/api-keys/{uuid.uuid4()}")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_list_organization_api_keys_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset()) | {"org:api_key:read"}
            ),
        }
    )
    organization_id = role_with_scope.organization_id
    assert organization_id is not None
    api_key = _organization_api_key_read(organization_id)
    page = CursorPaginatedResponse[OrganizationApiKeyRead](
        items=[api_key],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=1,
    )

    with patch.object(api_keys_router, "OrganizationApiKeyService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_keys.return_value = page
        MockService.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get("/organization/api-keys")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["items"][0]["id"] == str(api_key.id)
    assert payload["items"][0]["organization_id"] == str(organization_id)
