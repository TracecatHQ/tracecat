from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import tracecat.registry.common as registry_common
from tracecat.registry.constants import DEFAULT_LOCAL_REGISTRY_ORIGIN
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate


@pytest.mark.anyio
async def test_ensure_org_repositories_creates_repositories_without_entitlement(
    test_role,
) -> None:
    session = AsyncMock()

    with (
        patch.object(
            registry_common.config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True
        ),
        patch.object(registry_common.config, "TRACECAT__LOCAL_REPOSITORY_PATH", "/tmp"),
        patch.object(
            registry_common,
            "get_setting",
            new=AsyncMock(return_value="git+ssh://git@github.com/acme/repo.git"),
        ),
        patch.object(registry_common, "RegistryReposService") as MockReposService,
    ):
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository.return_value = None
        MockReposService.return_value = mock_repos_service

        await registry_common.ensure_org_repositories(session, test_role)

    MockReposService.assert_called_once()
    assert mock_repos_service.create_repository.await_count == 2


@pytest.mark.anyio
async def test_ensure_org_repositories_creates_local_and_remote(
    test_role,
) -> None:
    session = AsyncMock()
    remote_url = "git+ssh://git@github.com/acme/repo.git"

    with (
        patch.object(
            registry_common.config, "TRACECAT__LOCAL_REPOSITORY_ENABLED", True
        ),
        patch.object(registry_common.config, "TRACECAT__LOCAL_REPOSITORY_PATH", "/tmp"),
        patch.object(
            registry_common,
            "get_setting",
            new=AsyncMock(return_value=remote_url),
        ),
        patch.object(registry_common, "RegistryReposService") as MockReposService,
    ):
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository.return_value = None
        MockReposService.return_value = mock_repos_service

        await registry_common.ensure_org_repositories(session, test_role)

    expected = {
        DEFAULT_LOCAL_REGISTRY_ORIGIN,
        remote_url,
    }
    created = {
        call.args[0].origin
        for call in mock_repos_service.create_repository.await_args_list
        if isinstance(call.args[0], RegistryRepositoryCreate)
    }
    assert created == expected
