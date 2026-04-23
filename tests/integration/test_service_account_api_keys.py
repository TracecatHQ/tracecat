from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, get_args
from uuid import UUID

import httpx
import pytest
from fastapi import status
from sqlalchemy import delete, select

from tracecat.api.app import app
from tracecat.auth.dependencies import OrgUserOnlyRole
from tracecat.auth.types import Role
from tracecat.authz.seeding import seed_system_scopes
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Scope, ServiceAccount, ServiceAccountApiKey, Workspace
from tracecat.service_accounts.constants import API_KEY_HEADER_NAME
from tracecat.service_accounts.router import WorkspaceUserOnlyInPath

pytestmark = pytest.mark.usefixtures("db")


def _dependency_for_role(role_type: Any) -> Callable[..., Any]:
    for metadata in get_args(role_type):
        dependency = getattr(metadata, "dependency", None)
        if dependency is not None:
            return dependency
    raise AssertionError(f"Could not find dependency for {role_type!r}")


def _role_override(role: Role) -> Callable[[], Role]:
    def override() -> Role:
        ctx_role.set(role)
        return role

    return override


@contextmanager
def _dependency_overrides(
    overrides: dict[Callable[..., Any], Callable[..., Any]],
) -> Iterator[None]:
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(overrides)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@asynccontextmanager
async def _api_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.middleware_stack = None


async def _scope_ids_by_name(names: tuple[str, ...]) -> dict[str, str]:
    async with get_async_session_bypass_rls_context_manager() as session:
        await seed_system_scopes(session)
        result = await session.execute(select(Scope).where(Scope.name.in_(names)))
        scopes = {scope.name: str(scope.id) for scope in result.scalars().all()}

    missing = set(names) - set(scopes)
    assert not missing
    return scopes


async def _delete_service_accounts(service_account_ids: list[UUID]) -> None:
    if not service_account_ids:
        return
    async with get_async_session_bypass_rls_context_manager() as session:
        await session.execute(
            delete(ServiceAccount).where(ServiceAccount.id.in_(service_account_ids))
        )
        await session.commit()


async def _api_key_last_used_at(api_key_id: str) -> str | None:
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(
            select(ServiceAccountApiKey.last_used_at).where(
                ServiceAccountApiKey.id == UUID(api_key_id)
            )
        )
        last_used_at = result.scalar_one()
    return last_used_at.isoformat() if last_used_at is not None else None


async def _request_with_service_account_key(
    method: str,
    path: str,
    *,
    raw_key: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    token = ctx_role.set(None)
    try:
        async with _api_client() as client:
            return await client.request(
                method,
                path,
                params=params,
                headers={API_KEY_HEADER_NAME: raw_key},
            )
    finally:
        ctx_role.reset(token)


def _management_role(
    *,
    organization_id: UUID,
    workspace_id: UUID | None,
) -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        organization_id=organization_id,
        workspace_id=workspace_id,
        is_platform_superuser=True,
        scopes=frozenset({"*"}),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["workspace", "organization"])
async def test_service_account_key_created_over_http_authenticates_separate_client(
    kind: str,
    test_workspace: Workspace,
) -> None:
    organization_id = test_workspace.organization_id
    workspace_id = test_workspace.id
    created_service_account_ids: list[UUID] = []

    if kind == "workspace":
        role_dependency = _dependency_for_role(WorkspaceUserOnlyInPath)
        create_path = f"/workspaces/{workspace_id}/service-accounts"
        scope_names = ("workflow:read",)
        allowed_requests = [
            ("GET", "/workflows", {"workspace_id": str(workspace_id), "limit": 5})
        ]
        denied_request = ("GET", "/workspaces", None)
        service_account_base_path = f"/workspaces/{workspace_id}/service-accounts"
        role = _management_role(
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        expected_prefix = "tc_ws_sk_"
    else:
        role_dependency = _dependency_for_role(OrgUserOnlyRole)
        create_path = "/organization/service-accounts"
        scope_names = ("org:read", "org:workspace:read", "workflow:read")
        allowed_requests = [
            ("GET", "/workspaces", None),
            ("GET", "/workflows", {"workspace_id": str(workspace_id), "limit": 5}),
        ]
        denied_request = ("GET", "/organization/service-accounts", {"limit": 100})
        service_account_base_path = "/organization/service-accounts"
        role = _management_role(
            organization_id=organization_id,
            workspace_id=None,
        )
        expected_prefix = "tc_org_sk_"

    try:
        scope_ids = await _scope_ids_by_name(scope_names)
        overrides = {role_dependency: _role_override(role)}

        with _dependency_overrides(overrides):
            async with _api_client() as client:
                create_response = await client.post(
                    create_path,
                    json={
                        "name": f"{kind} integration key",
                        "description": "Created by service-account API key integration test",
                        "scope_ids": [scope_ids[name] for name in scope_names],
                        "initial_key_name": "Integration primary",
                    },
                )

        assert create_response.status_code == status.HTTP_201_CREATED
        create_payload = create_response.json()
        raw_key = create_payload["issued_api_key"]["raw_key"]
        assert raw_key.startswith(expected_prefix)
        service_account_id = UUID(create_payload["service_account"]["id"])
        api_key_id = create_payload["issued_api_key"]["api_key"]["id"]
        created_service_account_ids.append(service_account_id)

        ctx_role.set(None)
        async with _api_client() as client:
            for method, path, params in allowed_requests:
                response = await client.request(
                    method,
                    path,
                    params=params,
                    headers={API_KEY_HEADER_NAME: raw_key},
                )
                assert response.status_code == status.HTTP_200_OK

            denied_method, denied_path, denied_params = denied_request
            denied_response = await client.request(
                denied_method,
                denied_path,
                params=denied_params,
                headers={API_KEY_HEADER_NAME: raw_key},
            )
            assert denied_response.status_code == status.HTTP_403_FORBIDDEN

        assert await _api_key_last_used_at(api_key_id) is not None

        with _dependency_overrides(overrides):
            async with _api_client() as client:
                disable_response = await client.post(
                    f"{service_account_base_path}/{service_account_id}/disable"
                )

        assert disable_response.status_code == status.HTTP_204_NO_CONTENT

        ctx_role.set(None)
        async with _api_client() as client:
            after_disable_response = await client.request(
                allowed_requests[0][0],
                allowed_requests[0][1],
                params=allowed_requests[0][2],
                headers={API_KEY_HEADER_NAME: raw_key},
            )

        assert after_disable_response.status_code == status.HTTP_401_UNAUTHORIZED
    finally:
        await _delete_service_accounts(created_service_account_ids)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["workspace", "organization"])
async def test_service_account_rotation_and_revoke_invalidate_old_keys(
    kind: str,
    test_workspace: Workspace,
) -> None:
    organization_id = test_workspace.organization_id
    workspace_id = test_workspace.id
    created_service_account_ids: list[UUID] = []
    allowed_request = (
        "GET",
        "/workflows",
        {"workspace_id": str(workspace_id), "limit": 5},
    )

    if kind == "workspace":
        role_dependency = _dependency_for_role(WorkspaceUserOnlyInPath)
        create_path = f"/workspaces/{workspace_id}/service-accounts"
        service_account_base_path = f"/workspaces/{workspace_id}/service-accounts"
        role = _management_role(
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        expected_prefix = "tc_ws_sk_"
    else:
        role_dependency = _dependency_for_role(OrgUserOnlyRole)
        create_path = "/organization/service-accounts"
        service_account_base_path = "/organization/service-accounts"
        role = _management_role(
            organization_id=organization_id,
            workspace_id=None,
        )
        expected_prefix = "tc_org_sk_"

    try:
        scope_ids = await _scope_ids_by_name(("workflow:read",))
        overrides = {role_dependency: _role_override(role)}

        with _dependency_overrides(overrides):
            async with _api_client() as client:
                create_response = await client.post(
                    create_path,
                    json={
                        "name": f"{kind} rotation key",
                        "scope_ids": [scope_ids["workflow:read"]],
                        "initial_key_name": "Initial",
                    },
                )

        assert create_response.status_code == status.HTTP_201_CREATED
        create_payload = create_response.json()
        original_raw_key = create_payload["issued_api_key"]["raw_key"]
        assert original_raw_key.startswith(expected_prefix)
        service_account_id = UUID(create_payload["service_account"]["id"])
        created_service_account_ids.append(service_account_id)
        original_api_key_id = create_payload["issued_api_key"]["api_key"]["id"]

        method, path, params = allowed_request
        original_key_response = await _request_with_service_account_key(
            method,
            path,
            raw_key=original_raw_key,
            params=params,
        )
        assert original_key_response.status_code == status.HTTP_200_OK

        with _dependency_overrides(overrides):
            async with _api_client() as client:
                rotate_response = await client.post(
                    f"{service_account_base_path}/{service_account_id}/api-keys",
                    json={"name": "Rotated"},
                )

        assert rotate_response.status_code == status.HTTP_201_CREATED
        rotate_payload = rotate_response.json()
        rotated_raw_key = rotate_payload["issued_api_key"]["raw_key"]
        assert rotated_raw_key.startswith(expected_prefix)
        assert rotated_raw_key != original_raw_key
        assert rotate_payload["issued_api_key"]["api_key"]["id"] != original_api_key_id

        old_key_after_rotation_response = await _request_with_service_account_key(
            method,
            path,
            raw_key=original_raw_key,
            params=params,
        )
        assert (
            old_key_after_rotation_response.status_code == status.HTTP_401_UNAUTHORIZED
        )

        rotated_key_response = await _request_with_service_account_key(
            method,
            path,
            raw_key=rotated_raw_key,
            params=params,
        )
        assert rotated_key_response.status_code == status.HTTP_200_OK

        rotated_api_key_id = rotate_payload["issued_api_key"]["api_key"]["id"]
        with _dependency_overrides(overrides):
            async with _api_client() as client:
                revoke_response = await client.post(
                    f"{service_account_base_path}/{service_account_id}/api-keys/{rotated_api_key_id}/revoke"
                )

        assert revoke_response.status_code == status.HTTP_204_NO_CONTENT

        revoked_key_response = await _request_with_service_account_key(
            method,
            path,
            raw_key=rotated_raw_key,
            params=params,
        )
        assert revoked_key_response.status_code == status.HTTP_401_UNAUTHORIZED
    finally:
        await _delete_service_accounts(created_service_account_ids)
