from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tracecat.registry.common as registry_common
from tracecat.auth.types import Role
from tracecat.managed_resources.guard import ManagedResourceLockedError
from tracecat.registry.constants import DEFAULT_LOCAL_REGISTRY_ORIGIN
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate


@pytest.mark.anyio
async def test_ensure_org_repositories_skips_when_custom_registry_not_entitled(
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
        patch.object(
            registry_common,
            "is_org_entitled",
            new=AsyncMock(return_value=False),
        ) as mock_is_org_entitled,
        patch.object(registry_common, "RegistryReposService") as MockReposService,
    ):
        await registry_common.ensure_org_repositories(session, test_role)

    mock_is_org_entitled.assert_awaited_once()
    MockReposService.assert_not_called()


@pytest.mark.anyio
async def test_ensure_org_repositories_creates_local_and_remote_when_entitled(
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
        patch.object(
            registry_common,
            "is_org_entitled",
            new=AsyncMock(return_value=True),
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


OLD_URL = "git+ssh://git@github.com/acme/old-actions.git"
NEW_URL = "git+ssh://git@github.com/acme/new-actions.git"


def _role_with_scopes(role: Role, scopes: set[str]) -> Role:
    return role.model_copy(update={"scopes": frozenset(scopes)})


@pytest.mark.anyio
async def test_remove_replaced_custom_repository_deletes_previous_origin(
    test_role,
) -> None:
    role = _role_with_scopes(test_role, {"org:registry:delete"})
    repository = MagicMock()

    with patch.object(registry_common, "RegistryReposService") as MockReposService:
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository.return_value = repository
        MockReposService.return_value = mock_repos_service

        await registry_common.remove_replaced_custom_repository(
            AsyncMock(), role, previous_url=f"{OLD_URL}?ref=main", new_url=NEW_URL
        )

    mock_repos_service.get_repository.assert_awaited_once_with(OLD_URL)
    mock_repos_service.delete_repository.assert_awaited_once_with(repository)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("previous_url", "new_url"),
    [
        (None, NEW_URL),
        (OLD_URL, None),
        (OLD_URL, OLD_URL),
        (OLD_URL, f"{OLD_URL}?ref=dev"),
    ],
)
async def test_remove_replaced_custom_repository_keeps_repository_without_replacement(
    test_role, previous_url: str | None, new_url: str | None
) -> None:
    role = _role_with_scopes(test_role, {"org:registry:delete"})

    with patch.object(registry_common, "RegistryReposService") as MockReposService:
        await registry_common.remove_replaced_custom_repository(
            AsyncMock(), role, previous_url=previous_url, new_url=new_url
        )

    MockReposService.assert_not_called()


@pytest.mark.anyio
async def test_remove_replaced_custom_repository_requires_delete_scope(
    test_role,
) -> None:
    role = _role_with_scopes(test_role, {"org:settings:update"})

    with patch.object(registry_common, "RegistryReposService") as MockReposService:
        await registry_common.remove_replaced_custom_repository(
            AsyncMock(), role, previous_url=OLD_URL, new_url=NEW_URL
        )

    MockReposService.assert_not_called()


@pytest.mark.anyio
async def test_remove_replaced_custom_repository_keeps_managed_repository(
    test_role,
) -> None:
    role = _role_with_scopes(test_role, {"org:registry:delete"})
    repository = MagicMock()

    with patch.object(registry_common, "RegistryReposService") as MockReposService:
        mock_repos_service = AsyncMock()
        mock_repos_service.get_repository.return_value = repository
        mock_repos_service.delete_repository.side_effect = ManagedResourceLockedError(
            "registry_repository", repository.id
        )
        MockReposService.return_value = mock_repos_service

        await registry_common.remove_replaced_custom_repository(
            AsyncMock(), role, previous_url=OLD_URL, new_url=NEW_URL
        )

    mock_repos_service.delete_repository.assert_awaited_once_with(repository)
