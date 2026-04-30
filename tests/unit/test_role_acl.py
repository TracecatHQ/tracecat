from __future__ import annotations

import uuid
from collections.abc import Generator
from unittest.mock import ANY, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tracecat.auth import credentials
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session


@pytest.fixture
def role_acl_app(monkeypatch: pytest.MonkeyPatch) -> Generator[FastAPI, None, None]:
    app = FastAPI()
    session = AsyncMock()

    async def override_get_async_session() -> AsyncMock:
        return session

    async def override_optional_current_active_user() -> None:
        return None

    app.dependency_overrides[get_async_session] = override_get_async_session
    app.dependency_overrides[credentials.optional_current_active_user] = (
        override_optional_current_active_user
    )
    monkeypatch.setattr(credentials, "set_rls_context", AsyncMock())
    monkeypatch.setattr(credentials, "set_rls_context_from_role", AsyncMock())

    token = ctx_role.set(None)
    try:
        yield app
    finally:
        ctx_role.reset(token)
        app.dependency_overrides.clear()


def test_role_acl_extracts_internal_service_key_for_service_only_route(
    role_acl_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(credentials.config, "TRACECAT__SERVICE_KEY", "service-secret")
    monkeypatch.setattr(
        credentials.config,
        "TRACECAT__SERVICE_ROLES_WHITELIST",
        ["tracecat-service"],
    )

    @role_acl_app.get("/service")
    async def service_route(  # pyright: ignore[reportUnusedFunction] - route handler
        role: Role = RoleACL(
            allow_user=False,
            allow_service=True,
            require_workspace="no",
        ),
    ) -> dict[str, str]:
        return {"role_type": role.type, "service_id": role.service_id}

    response = TestClient(role_acl_app).get(
        "/service",
        headers={
            "x-tracecat-service-key": "service-secret",
            "x-tracecat-role-service-id": "tracecat-service",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "role_type": "service",
        "service_id": "tracecat-service",
    }


def test_role_acl_threads_tracecat_api_key_and_workspace_query(
    role_acl_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    service_account_role = Role(
        type="service_account",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=workspace_id,
        bound_workspace_id=workspace_id,
        service_account_id=uuid.uuid4(),
        scopes=frozenset({"workflow:read"}),
    )
    authenticate_api_key = AsyncMock(return_value=service_account_role)
    monkeypatch.setattr(credentials, "_authenticate_api_key", authenticate_api_key)

    @role_acl_app.get("/api-key")
    async def api_key_route(  # pyright: ignore[reportUnusedFunction] - route handler
        role: Role = RoleACL(
            allow_user=False,
            allow_api_key=True,
            require_workspace="yes",
        ),
    ) -> dict[str, str]:
        assert role.workspace_id is not None
        return {
            "role_type": role.type,
            "workspace_id": str(role.workspace_id),
        }

    response = TestClient(role_acl_app).get(
        "/api-key",
        params={"workspace_id": str(workspace_id)},
        headers={"Authorization": "Bearer tc_ws_sk_managed-api-key_secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "role_type": "service_account",
        "workspace_id": str(workspace_id),
    }
    authenticate_api_key.assert_awaited_once_with(
        api_key="tc_ws_sk_managed-api-key_secret",
        workspace_id=workspace_id,
    )


def test_role_acl_rejects_legacy_tracecat_api_key_header(
    role_acl_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticate_api_key = AsyncMock()
    monkeypatch.setattr(credentials, "_authenticate_api_key", authenticate_api_key)

    @role_acl_app.get("/api-key-legacy")
    async def api_key_legacy_route(  # pyright: ignore[reportUnusedFunction] - route handler
        role: Role = RoleACL(
            allow_user=False,
            allow_api_key=True,
            require_workspace="no",
        ),
    ) -> dict[str, str]:
        return {"role_type": role.type}

    response = TestClient(role_acl_app).get(
        "/api-key-legacy",
        headers={"x-tracecat-api-key": "tc_ws_sk_managed-api-key_secret"},
    )

    assert response.status_code == 403
    authenticate_api_key.assert_not_awaited()


def test_role_acl_combined_service_and_api_key_route_uses_service_key_fallback(
    role_acl_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_role = Role(
        type="service",
        service_id="tracecat-service",
        scopes=frozenset({"workflow:read"}),
    )
    authenticate_service = AsyncMock(return_value=service_role)
    authenticate_api_key = AsyncMock(return_value=None)
    monkeypatch.setattr(credentials, "_authenticate_service", authenticate_service)
    monkeypatch.setattr(credentials, "_authenticate_api_key", authenticate_api_key)

    @role_acl_app.get("/either")
    async def either_route(  # pyright: ignore[reportUnusedFunction] - route handler
        role: Role = RoleACL(
            allow_user=False,
            allow_service=True,
            allow_api_key=True,
            require_workspace="no",
        ),
    ) -> dict[str, str]:
        return {"role_type": role.type, "service_id": role.service_id}

    response = TestClient(role_acl_app).get(
        "/either",
        headers={
            "x-tracecat-service-key": "service-secret",
            "x-tracecat-role-service-id": "tracecat-service",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "role_type": "service",
        "service_id": "tracecat-service",
    }
    authenticate_api_key.assert_not_awaited()
    authenticate_service.assert_awaited_once_with(ANY, "service-secret")


def test_role_acl_prefers_tracecat_api_key_when_both_headers_are_present(
    role_acl_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_account_role = Role(
        type="service_account",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        service_account_id=uuid.uuid4(),
        scopes=frozenset({"org:read"}),
    )
    authenticate_api_key = AsyncMock(return_value=service_account_role)
    authenticate_service = AsyncMock()
    monkeypatch.setattr(credentials, "_authenticate_api_key", authenticate_api_key)
    monkeypatch.setattr(credentials, "_authenticate_service", authenticate_service)

    @role_acl_app.get("/either-priority")
    async def either_priority_route(  # pyright: ignore[reportUnusedFunction] - route handler
        role: Role = RoleACL(
            allow_user=False,
            allow_service=True,
            allow_api_key=True,
            require_workspace="no",
        ),
    ) -> dict[str, str]:
        return {"role_type": role.type, "service_id": role.service_id}

    response = TestClient(role_acl_app).get(
        "/either-priority",
        headers={
            "Authorization": "Bearer tc_org_sk_managed-api-key_secret",
            "x-tracecat-service-key": "service-secret",
            "x-tracecat-role-service-id": "tracecat-service",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "role_type": "service_account",
        "service_id": "tracecat-api",
    }
    authenticate_api_key.assert_awaited_once_with(
        api_key="tc_org_sk_managed-api-key_secret",
        workspace_id=None,
    )
    authenticate_service.assert_not_awaited()
