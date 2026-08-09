"""Tests for superuser organization registry sync behavior."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.admin.organizations.service import AdminOrgService

from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import RegistryRepository
from tracecat.registry.actions.types import RepositorySyncOutcome
from tracecat.ssh import SshEnv


@pytest.mark.anyio
async def test_admin_git_sync_forwards_configured_package_name(mocker) -> None:
    organization_id = uuid.uuid4()
    repository_id = uuid.uuid4()
    repository = RegistryRepository(
        id=repository_id,
        organization_id=organization_id,
        origin="git+ssh://git@git.example.test/acme/custom-registry.git",
    )
    repository_result = mocker.Mock()
    repository_result.scalar_one_or_none.return_value = repository
    refreshed_repository = mocker.Mock(actions=[mocker.Mock(), mocker.Mock()])
    refreshed_result = mocker.Mock()
    refreshed_result.scalar_one.return_value = refreshed_repository
    session = mocker.MagicMock(spec=AsyncSession)
    session.execute = mocker.AsyncMock(
        side_effect=[repository_result, refreshed_result]
    )

    repos_service = mocker.Mock(
        update_repository=mocker.AsyncMock(return_value=repository)
    )
    mocker.patch(
        "tracecat.registry.repositories.service.RegistryReposService",
        return_value=repos_service,
    )
    mocker.patch(
        "tracecat.registry.versions.service.RegistryVersionsService",
        return_value=mocker.Mock(),
    )
    actions_service = mocker.Mock(
        sync_actions_from_repository=mocker.AsyncMock(
            return_value=RepositorySyncOutcome(
                commit_sha="a" * 40,
                version="2026.08.09",
            )
        )
    )
    mocker.patch(
        "tracecat.registry.actions.service.RegistryActionsService",
        return_value=actions_service,
    )

    requested_settings: list[str] = []

    async def fake_get_setting(name: str, *, role: Role):
        assert role.organization_id == organization_id
        requested_settings.append(name)
        if name == "git_repo_package_name":
            return "custom_registry"
        if name == "git_allowed_domains":
            return {"git.example.test"}
        raise AssertionError(f"Unexpected setting: {name}")

    mocker.patch(
        "tracecat.settings.service.get_setting",
        side_effect=fake_get_setting,
    )
    ssh_env = SshEnv(
        ssh_auth_sock="/tmp/synthetic-agent.sock",
        ssh_agent_pid="123",
    )

    @asynccontextmanager
    async def fake_ssh_context(**_kwargs):
        yield ssh_env

    mocker.patch("tracecat.ssh.ssh_context", side_effect=fake_ssh_context)

    service = AdminOrgService(
        session,
        role=PlatformRole(
            type="user",
            user_id=uuid.uuid4(),
            service_id="tracecat-api",
        ),
    )
    mocker.patch.object(service, "_require_organization", mocker.AsyncMock())
    raw_sync = cast(Any, AdminOrgService.sync_org_repository).__wrapped__

    response = await raw_sync(service, organization_id, repository_id, force=False)

    assert requested_settings == [
        "git_repo_package_name",
        "git_allowed_domains",
    ]
    actions_service.sync_actions_from_repository.assert_awaited_once_with(
        repository,
        git_repo_package_name="custom_registry",
        ssh_env=ssh_env,
    )
    assert response.commit_sha == "a" * 40
    assert response.version == "2026.08.09"
    assert response.actions_count == 2
    repos_service.update_repository.assert_awaited_once()
