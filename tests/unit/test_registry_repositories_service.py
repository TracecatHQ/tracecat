"""Tests for registry repository service behavior."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import RegistryRepository
from tracecat.exceptions import RegistryNotFound, ScopeDeniedError
from tracecat.registry.actions.types import RepositorySyncOutcome
from tracecat.registry.repositories.schemas import RegistryRepositorySync
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.ssh import SshEnv


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"org:registry:delete"}),
    )


@pytest.fixture
def role_with_read_only_scope() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"org:registry:read"}),
    )


@pytest.fixture
def role_without_registry_scopes() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"workflow:read"}),
    )


@pytest.fixture
def role_with_update_scope() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"org:registry:update"}),
    )


@pytest.mark.anyio
async def test_delete_repository_clears_promoted_version_before_delete(
    role: Role,
) -> None:
    """Deleting a promoted repository must clear the self-referential FK first."""
    session = AsyncMock()
    repository = RegistryRepository(
        organization_id=role.organization_id,
        origin="test_origin",
    )
    repository.current_version_id = uuid.uuid4()

    service = RegistryReposService(session, role=role)
    await service.delete_repository(repository)

    assert repository.current_version_id is None
    assert session.mock_calls == [
        call.flush(),
        call.delete(repository),
        call.commit(),
    ]


@pytest.mark.anyio
async def test_delete_repository_without_promoted_version_skips_flush(
    role: Role,
) -> None:
    """Deleting an unpromoted repository should not emit the extra flush."""
    session = AsyncMock()
    repository = RegistryRepository(
        organization_id=role.organization_id,
        origin="test_origin",
    )

    service = RegistryReposService(session, role=role)
    await service.delete_repository(repository)

    assert session.mock_calls == [
        call.delete(repository),
        call.commit(),
    ]


@pytest.mark.anyio
async def test_sync_repository_requires_registry_update_scope(
    role_with_read_only_scope: Role,
) -> None:
    """sync_repository must reject roles missing org:registry:update."""
    service = RegistryReposService(AsyncMock(), role=role_with_read_only_scope)
    repository = RegistryRepository(
        organization_id=role_with_read_only_scope.organization_id,
        origin="custom_actions",
    )
    with pytest.raises(ScopeDeniedError):
        await service.sync_repository(repository, RegistryRepositorySync(force=False))


@pytest.mark.anyio
async def test_list_repositories_requires_registry_read_scope(
    role_without_registry_scopes: Role,
) -> None:
    """list_repositories must reject roles missing org:registry:read."""
    service = RegistryReposService(AsyncMock(), role=role_without_registry_scopes)
    with pytest.raises(ScopeDeniedError):
        await service.list_repositories()


@pytest.mark.anyio
async def test_sync_repository_rejects_cross_org_repository() -> None:
    """Cross-org repository must surface RegistryNotFound (probing-resistant)."""
    role_with_update = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"org:registry:update"}),
    )
    service = RegistryReposService(AsyncMock(), role=role_with_update)
    foreign_repository = RegistryRepository(
        organization_id=uuid.uuid4(),  # different org
        origin="custom_actions",
    )

    with pytest.raises(RegistryNotFound):
        await service.sync_repository(
            foreign_repository, RegistryRepositorySync(force=False)
        )


@pytest.mark.anyio
async def test_temporal_git_sync_skips_api_ssh_context(
    role_with_update_scope: Role,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", True)
    session = mocker.MagicMock(spec=AsyncSession)
    repository = RegistryRepository(
        id=uuid.uuid4(),
        organization_id=role_with_update_scope.organization_id,
        origin="git+ssh://git@git.example.test/acme/registry.git",
    )
    actions_service = mocker.Mock(
        sync_actions_from_repository=mocker.AsyncMock(
            return_value=RepositorySyncOutcome(
                commit_sha="a" * 40, version="2026.08.07"
            )
        ),
        list_actions_from_index_by_repository=mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "tracecat.registry.actions.service.RegistryActionsService",
        return_value=actions_service,
    )
    mocker.patch(
        "tracecat.registry.repositories.service.check_entitlement",
        mocker.AsyncMock(),
    )
    mocker.patch(
        "tracecat.registry.repositories.service.get_setting",
        mocker.AsyncMock(side_effect=["registry", {"git.example.test"}]),
    )
    ssh_context = mocker.patch("tracecat.registry.repositories.service.ssh_context")
    service = RegistryReposService(session, role=role_with_update_scope)
    mocker.patch.object(
        service,
        "update_repository",
        mocker.AsyncMock(return_value=repository),
    )

    result = await service.sync_repository(repository)

    assert result.commit_sha == "a" * 40
    ssh_context.assert_not_called()
    actions_service.sync_actions_from_repository.assert_awaited_once_with(
        repository,
        target_commit_sha=None,
        git_repo_package_name="registry",
    )


@pytest.mark.anyio
async def test_direct_git_sync_preserves_api_ssh_context(
    role_with_update_scope: Role,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", False)
    session = mocker.MagicMock(spec=AsyncSession)
    repository = RegistryRepository(
        id=uuid.uuid4(),
        organization_id=role_with_update_scope.organization_id,
        origin="git+ssh://git@git.example.test/acme/registry.git",
    )
    actions_service = mocker.Mock(
        sync_actions_from_repository=mocker.AsyncMock(
            return_value=RepositorySyncOutcome(
                commit_sha="b" * 40, version="2026.08.07"
            )
        ),
        list_actions_from_index_by_repository=mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "tracecat.registry.actions.service.RegistryActionsService",
        return_value=actions_service,
    )
    mocker.patch(
        "tracecat.registry.repositories.service.check_entitlement",
        mocker.AsyncMock(),
    )
    mocker.patch(
        "tracecat.registry.repositories.service.get_setting",
        mocker.AsyncMock(side_effect=["registry", {"git.example.test"}]),
    )
    ssh_env = SshEnv(ssh_auth_sock="/tmp/synthetic-agent.sock", ssh_agent_pid="123")

    @asynccontextmanager
    async def fake_ssh_context(**_kwargs):
        yield ssh_env

    ssh_context = mocker.patch(
        "tracecat.registry.repositories.service.ssh_context",
        side_effect=fake_ssh_context,
    )
    service = RegistryReposService(session, role=role_with_update_scope)
    mocker.patch.object(
        service,
        "update_repository",
        mocker.AsyncMock(return_value=repository),
    )

    result = await service.sync_repository(repository)

    assert result.commit_sha == "b" * 40
    ssh_context.assert_called_once()
    actions_service.sync_actions_from_repository.assert_awaited_once_with(
        repository,
        target_commit_sha=None,
        git_repo_package_name="registry",
        ssh_env=ssh_env,
    )
