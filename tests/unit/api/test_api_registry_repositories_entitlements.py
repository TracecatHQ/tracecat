"""Tests that organization registry routes remain core functionality."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

import tracecat.registry.repositories.router as repos_router_module
from tracecat.auth.types import Role
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryCreate,
    RegistryRepositoryUpdate,
)


def _repository(repository_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=repository_id,
        origin="git+ssh://git@github.com/acme/custom-registry.git",
        last_synced_at=None,
        commit_sha=None,
        current_version_id=None,
    )


@pytest.mark.anyio
async def test_create_registry_repository_is_available_to_scoped_org_actor(
    test_admin_role: Role,
) -> None:
    repository_id = uuid4()
    repository = _repository(repository_id)
    repos_service = MagicMock()
    repos_service.create_repository = AsyncMock(return_value=repository)

    with patch.object(
        repos_router_module,
        "RegistryReposService",
        return_value=repos_service,
    ):
        response = await repos_router_module.create_registry_repository(
            role=test_admin_role,
            session=AsyncMock(),
            params=RegistryRepositoryCreate(origin=repository.origin),
        )

    assert response.id == repository_id
    assert response.origin == repository.origin
    repos_service.create_repository.assert_awaited_once()


@pytest.mark.anyio
async def test_update_registry_repository_is_available_to_scoped_org_actor(
    test_admin_role: Role,
) -> None:
    repository_id = uuid4()
    repository = _repository(repository_id)
    repos_service = MagicMock()
    repos_service.get_repository_by_id = AsyncMock(return_value=repository)
    repos_service.update_repository = AsyncMock(return_value=repository)
    actions_service = MagicMock()
    actions_service.list_actions_from_index_by_repository = AsyncMock(return_value=[])

    with (
        patch.object(
            repos_router_module,
            "RegistryReposService",
            return_value=repos_service,
        ),
        patch.object(
            repos_router_module,
            "RegistryActionsService",
            return_value=actions_service,
        ),
    ):
        response = await repos_router_module.update_registry_repository(
            role=test_admin_role,
            session=AsyncMock(),
            repository_id=repository_id,
            params=RegistryRepositoryUpdate(commit_sha="a" * 40),
        )

    assert response.id == repository_id
    repos_service.update_repository.assert_awaited_once_with(
        repository, RegistryRepositoryUpdate(commit_sha="a" * 40)
    )
