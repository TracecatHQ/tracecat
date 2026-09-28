"""HTTP-level tests for registry repository commit listing."""

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.auth.credentials as auth_credentials_module
import tracecat.registry.repositories.router as repos_router_module
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatCredentialsNotFoundError


@pytest.fixture
def mock_role_acl_dependency(
    test_admin_role: Role,
) -> Generator[AsyncMock, None, None]:
    """Bypass RoleACL auth for route-level tests."""
    with patch.object(
        auth_credentials_module, "_role_dependency", new_callable=AsyncMock
    ) as mock_role_dependency:
        mock_role_dependency.return_value = test_admin_role
        yield mock_role_dependency


@asynccontextmanager
async def _no_db_session() -> AsyncIterator[None]:
    yield None


class _MissingSshKeyContext:
    """Stand-in for `ssh_context` when the org has no registry SSH key."""

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> None:
        raise TracecatCredentialsNotFoundError("No SSH key found")

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.anyio
async def test_list_repository_commits_without_ssh_key_returns_400(
    client: TestClient, test_admin_role: Role, mock_role_acl_dependency: AsyncMock
) -> None:
    repository_id = uuid4()
    repository = SimpleNamespace(
        id=repository_id,
        origin="git+ssh://git@github.com/acme/custom-registry.git",
    )

    with (
        patch.object(repos_router_module, "RegistryReposService") as MockReposService,
        patch.object(
            repos_router_module, "get_setting", new_callable=AsyncMock
        ) as mock_get_setting,
        patch.object(
            repos_router_module, "get_async_session_context_manager", _no_db_session
        ),
        patch.object(repos_router_module, "ssh_context", _MissingSshKeyContext),
    ):
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository_by_id.return_value = repository
        MockReposService.return_value = mock_repos_service
        mock_get_setting.return_value = {"github.com"}

        response = client.get(f"/registry/repos/{repository_id}/commits")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "No registry SSH key configured"
