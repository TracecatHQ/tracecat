"""HTTP-level tests for service account endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.schemas import UserReadMinimal, UserRole
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse
from tracecat.service_accounts import router as service_accounts_router
from tracecat.service_accounts.schemas import (
    ServiceAccountApiKeyRead,
    ServiceAccountScopeRead,
)


def _scope_read(name: str, *, action: str = "read") -> ServiceAccountScopeRead:
    resource = name.rsplit(":", maxsplit=1)[0]
    return ServiceAccountScopeRead(
        id=uuid.uuid4(),
        name=name,
        resource=resource,
        action=action,
        description=f"{name} description",
    )


def _service_account_read(
    organization_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID | None,
    key_prefix: str,
    name: str,
    owner_user_id: uuid.UUID | None = None,
    api_key_created_by: uuid.UUID | None = None,
):
    now = datetime(2024, 1, 1, tzinfo=UTC)
    scope = _scope_read("workflow:read")
    revoked_at = datetime(2024, 1, 2, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        name=name,
        description="test service account",
        disabled_at=None,
        created_at=now,
        updated_at=now,
        scopes=[scope],
        api_keys=[
            SimpleNamespace(
                id=uuid.uuid4(),
                name="Legacy",
                key_id="key_001",
                preview=f"{key_prefix}...wxyz",
                created_by=api_key_created_by,
                revoked_by=None,
                last_used_at=now,
                revoked_at=revoked_at,
                created_at=now,
                updated_at=revoked_at,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                name="Primary",
                key_id="key_123",
                preview=f"{key_prefix}...abcd",
                created_by=api_key_created_by,
                revoked_by=None,
                last_used_at=revoked_at,
                revoked_at=None,
                created_at=revoked_at,
                updated_at=revoked_at,
            ),
        ],
    )


def _organization_service_account_read(organization_id: uuid.UUID):
    return _service_account_read(
        organization_id,
        workspace_id=None,
        key_prefix="tc_org_sk_",
        name="Org automation",
    )


def _workspace_service_account_read(
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
):
    return _service_account_read(
        organization_id,
        workspace_id=workspace_id,
        key_prefix="tc_ws_sk_",
        name="Workspace automation",
    )


def _api_key_read(name: str, preview: str) -> ServiceAccountApiKeyRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return ServiceAccountApiKeyRead(
        id=uuid.uuid4(),
        name=name,
        key_id=f"{name.lower()}_id",
        preview=preview,
        created_by=None,
        created_by_user=None,
        revoked_by=None,
        revoked_by_user=None,
        last_used_at=None,
        revoked_at=None,
        created_at=now,
        updated_at=now,
    )


def _user_read_minimal(user_id: uuid.UUID, email: str) -> UserReadMinimal:
    return UserReadMinimal(
        id=user_id,
        email=email,
        role=UserRole.BASIC,
        first_name="Alice",
        last_name="Example",
    )


def _strip_org_service_account_read_access(
    scopes: frozenset[str] | None,
) -> frozenset[str]:
    return frozenset(
        scope
        for scope in (scopes or frozenset())
        if scope not in {"org:service_account:read", "org:service_account:update"}
    )


def _without_scope(scopes: frozenset[str] | None, denied_scope: str) -> frozenset[str]:
    return frozenset(
        scope for scope in (scopes or frozenset()) if scope != denied_scope
    )


@pytest.mark.anyio
async def test_list_organization_service_accounts_requires_read_scope(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_without_read = test_admin_role.model_copy(
        update={
            "scopes": _strip_org_service_account_read_access(test_admin_role.scopes)
        }
    )
    token = ctx_role.set(role_without_read)
    try:
        response = client.get("/organization/service-accounts")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_list_organization_service_account_scopes_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:read"}
            ),
        }
    )
    scope = _scope_read("workflow:read")

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_assignable_scopes.return_value = [scope]
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get("/organization/service-accounts/scopes")
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
async def test_get_organization_service_account_requires_read_scope(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_without_read = test_admin_role.model_copy(
        update={
            "scopes": _strip_org_service_account_read_access(test_admin_role.scopes)
        }
    )
    token = ctx_role.set(role_without_read)
    try:
        response = client.get(f"/organization/service-accounts/{uuid.uuid4()}")
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_list_organization_service_accounts_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:read"}
            ),
        }
    )
    organization_id = role_with_scope.organization_id
    assert organization_id is not None
    service_account = _organization_service_account_read(organization_id)
    page = CursorPaginatedResponse(
        items=[service_account],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=1,
    )

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_service_accounts.return_value = page
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get("/organization/service-accounts")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["items"][0]["id"] == str(service_account.id)
    assert payload["items"][0]["organization_id"] == str(organization_id)
    assert payload["items"][0]["active_api_key"]["name"] == "Primary"
    assert payload["items"][0]["api_key_counts"] == {
        "total": 2,
        "active": 1,
        "revoked": 1,
    }


@pytest.mark.anyio
async def test_list_organization_service_accounts_includes_creator_users(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:read"}
            ),
        }
    )
    organization_id = role_with_scope.organization_id
    assert organization_id is not None
    creator_id = uuid.uuid4()
    service_account = _service_account_read(
        organization_id,
        workspace_id=None,
        key_prefix="tc_org_sk_",
        name="Org automation",
        owner_user_id=creator_id,
        api_key_created_by=creator_id,
    )
    page = CursorPaginatedResponse(
        items=[service_account],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=1,
    )
    creator = _user_read_minimal(creator_id, "alice@example.com")

    with (
        patch.object(
            service_accounts_router, "OrganizationServiceAccountService"
        ) as mock_service_cls,
        patch.object(
            service_accounts_router, "_load_users_by_ids", new=AsyncMock()
        ) as mock_load_users,
    ):
        mock_svc = AsyncMock()
        mock_svc.list_service_accounts.return_value = page
        mock_service_cls.return_value = mock_svc
        mock_load_users.return_value = {creator_id: creator}

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get("/organization/service-accounts")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["items"][0]["owner_user"] == {
        "id": str(creator_id),
        "email": "alice@example.com",
        "role": "basic",
        "first_name": "Alice",
        "last_name": "Example",
    }
    assert payload["items"][0]["active_api_key"]["created_by_user"] == {
        "id": str(creator_id),
        "email": "alice@example.com",
        "role": "basic",
        "first_name": "Alice",
        "last_name": "Example",
    }


@pytest.mark.anyio
async def test_list_organization_service_account_api_keys_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:read"}
            ),
        }
    )
    api_key = _api_key_read("Primary", "tc_org_sk_...abcd")
    page = CursorPaginatedResponse(
        items=[api_key],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=1,
    )

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_service_account_api_keys.return_value = page
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get(
                f"/organization/service-accounts/{uuid.uuid4()}/api-keys"
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["preview"] == "tc_org_sk_...abcd"


@pytest.mark.anyio
async def test_list_organization_service_account_api_keys_includes_creator_users(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:read"}
            ),
        }
    )
    creator_id = uuid.uuid4()
    api_key = _api_key_read("Primary", "tc_org_sk_...abcd").model_copy(
        update={"created_by": creator_id}
    )
    page = CursorPaginatedResponse(
        items=[api_key],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=1,
    )
    creator = _user_read_minimal(creator_id, "alice@example.com")

    with (
        patch.object(
            service_accounts_router, "OrganizationServiceAccountService"
        ) as mock_service_cls,
        patch.object(
            service_accounts_router, "_load_users_by_ids", new=AsyncMock()
        ) as mock_load_users,
    ):
        mock_svc = AsyncMock()
        mock_svc.list_service_account_api_keys.return_value = page
        mock_service_cls.return_value = mock_svc
        mock_load_users.return_value = {creator_id: creator}

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get(
                f"/organization/service-accounts/{uuid.uuid4()}/api-keys"
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["created_by_user"] == {
        "id": str(creator_id),
        "email": "alice@example.com",
        "role": "basic",
        "first_name": "Alice",
        "last_name": "Example",
    }


@pytest.mark.anyio
async def test_list_organization_service_accounts_hides_internal_errors(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:read"}
            ),
        }
    )

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_service_accounts.side_effect = RuntimeError(
            "constraint service_account_name_key"
        )
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get("/organization/service-accounts")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json() == {"detail": "Internal server error"}


@pytest.mark.anyio
async def test_list_organization_service_accounts_returns_bad_request_for_validation_errors(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:read"}
            ),
        }
    )

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_service_accounts.side_effect = TracecatValidationError(
            "Invalid cursor for service accounts"
        )
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get("/organization/service-accounts")
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Invalid cursor for service accounts"}


@pytest.mark.anyio
async def test_create_organization_service_account_requires_create_scope(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_without_create = test_admin_role.model_copy(
        update={
            "scopes": _without_scope(
                test_admin_role.scopes, "org:service_account:create"
            )
        }
    )

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        token = ctx_role.set(role_without_create)
        try:
            response = client.post(
                "/organization/service-accounts",
                json={"name": "Org automation"},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_service_cls.assert_not_called()


@pytest.mark.anyio
async def test_create_organization_service_account_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:create"}
            ),
        }
    )
    organization_id = role_with_scope.organization_id
    assert organization_id is not None
    service_account = _organization_service_account_read(organization_id)
    api_key = _api_key_read("Primary", "tc_org_sk_...abcd")

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.create_service_account.return_value = (
            service_account,
            api_key,
            "raw-secret",
        )
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.post(
                "/organization/service-accounts",
                json={"name": "Org automation"},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_201_CREATED
    payload = response.json()
    assert payload["issued_api_key"]["raw_key"] == "raw-secret"
    assert payload["issued_api_key"]["api_key"]["preview"] == "tc_org_sk_...abcd"
    assert payload["service_account"]["active_api_key"]["name"] == "Primary"
    assert payload["service_account"]["api_key_counts"] == {
        "total": 2,
        "active": 1,
        "revoked": 1,
    }


@pytest.mark.anyio
async def test_create_organization_service_account_api_key_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:update"}
            ),
        }
    )
    organization_id = role_with_scope.organization_id
    assert organization_id is not None
    service_account = _organization_service_account_read(organization_id)
    api_key = _api_key_read("Primary", "tc_org_sk_...abcd")

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.issue_api_key.return_value = (service_account, api_key, "raw-secret")
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.post(
                f"/organization/service-accounts/{service_account.id}/api-keys",
                json={"name": "Primary"},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["issued_api_key"] == {
        "raw_key": "raw-secret",
        "api_key": {
            "id": str(api_key.id),
            "name": "Primary",
            "key_id": "primary_id",
            "preview": "tc_org_sk_...abcd",
            "created_by": None,
            "created_by_user": None,
            "revoked_by": None,
            "revoked_by_user": None,
            "last_used_at": None,
            "revoked_at": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        },
    }


@pytest.mark.anyio
async def test_revoke_organization_service_account_api_key_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"org:service_account:update"}
            ),
        }
    )
    service_account_id = uuid.uuid4()
    api_key_id = uuid.uuid4()

    with patch.object(
        service_accounts_router, "OrganizationServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.post(
                f"/organization/service-accounts/{service_account_id}/api-keys/{api_key_id}/revoke"
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_svc.revoke_api_key.assert_awaited_once_with(service_account_id, api_key_id)


@pytest.mark.anyio
async def test_create_workspace_service_account_requires_create_scope(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None
    role_without_create = test_admin_role.model_copy(
        update={
            "scopes": _without_scope(
                test_admin_role.scopes, "workspace:service_account:create"
            )
        }
    )

    with patch.object(
        service_accounts_router, "WorkspaceServiceAccountService"
    ) as mock_service_cls:
        token = ctx_role.set(role_without_create)
        try:
            response = client.post(
                f"/workspaces/{workspace_id}/service-accounts",
                json={"name": "Workspace automation"},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_service_cls.assert_not_called()


@pytest.mark.anyio
async def test_create_workspace_service_account_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    organization_id = test_admin_role.organization_id
    assert workspace_id is not None
    assert organization_id is not None
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"workspace:service_account:create"}
            ),
        }
    )
    service_account = _workspace_service_account_read(organization_id, workspace_id)
    api_key = _api_key_read("Primary", "tc_ws_sk_...abcd")

    with patch.object(
        service_accounts_router, "WorkspaceServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.create_service_account.return_value = (
            service_account,
            api_key,
            "raw-secret",
        )
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.post(
                f"/workspaces/{workspace_id}/service-accounts",
                json={"name": "Workspace automation"},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_201_CREATED
    payload = response.json()
    assert payload["issued_api_key"]["raw_key"] == "raw-secret"
    assert payload["issued_api_key"]["api_key"]["preview"] == "tc_ws_sk_...abcd"
    assert payload["service_account"]["workspace_id"] == str(workspace_id)
    assert payload["service_account"]["active_api_key"]["name"] == "Primary"


@pytest.mark.anyio
async def test_list_workspace_service_account_api_keys_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"workspace:service_account:read"}
            ),
        }
    )
    api_key = _api_key_read("Primary", "tc_ws_sk_...abcd")
    page = CursorPaginatedResponse(
        items=[api_key],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
        total_estimate=1,
    )

    with patch.object(
        service_accounts_router, "WorkspaceServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_service_account_api_keys.return_value = page
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.get(
                f"/workspaces/{workspace_id}/service-accounts/{uuid.uuid4()}/api-keys"
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["preview"] == "tc_ws_sk_...abcd"


@pytest.mark.anyio
async def test_create_workspace_service_account_api_key_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    organization_id = test_admin_role.organization_id
    assert workspace_id is not None
    assert organization_id is not None
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"workspace:service_account:update"}
            ),
        }
    )
    service_account = _workspace_service_account_read(organization_id, workspace_id)
    api_key = _api_key_read("Primary", "tc_ws_sk_...abcd")

    with patch.object(
        service_accounts_router, "WorkspaceServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.issue_api_key.return_value = (service_account, api_key, "raw-secret")
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.post(
                f"/workspaces/{workspace_id}/service-accounts/{service_account.id}/api-keys",
                json={"name": "Primary"},
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["issued_api_key"] == {
        "raw_key": "raw-secret",
        "api_key": {
            "id": str(api_key.id),
            "name": "Primary",
            "key_id": "primary_id",
            "preview": "tc_ws_sk_...abcd",
            "created_by": None,
            "created_by_user": None,
            "revoked_by": None,
            "revoked_by_user": None,
            "last_used_at": None,
            "revoked_at": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        },
    }


@pytest.mark.anyio
async def test_revoke_workspace_service_account_api_key_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    workspace_id = test_admin_role.workspace_id
    assert workspace_id is not None
    role_with_scope = test_admin_role.model_copy(
        update={
            "scopes": frozenset(
                set(test_admin_role.scopes or frozenset())
                | {"workspace:service_account:update"}
            ),
        }
    )
    service_account_id = uuid.uuid4()
    api_key_id = uuid.uuid4()

    with patch.object(
        service_accounts_router, "WorkspaceServiceAccountService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_service_cls.return_value = mock_svc

        token = ctx_role.set(role_with_scope)
        try:
            response = client.post(
                f"/workspaces/{workspace_id}/service-accounts/{service_account_id}/api-keys/{api_key_id}/revoke"
            )
        finally:
            ctx_role.reset(token)

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_svc.revoke_api_key.assert_awaited_once_with(service_account_id, api_key_id)
