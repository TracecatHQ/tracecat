"""Tests for registry repository service behavior."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, call

import pytest

from tracecat.auth.types import Role
from tracecat.db.models import RegistryRepository
from tracecat.exceptions import ScopeDeniedError
from tracecat.registry.repositories.schemas import RegistryRepositorySync
from tracecat.registry.repositories.service import RegistryReposService


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
