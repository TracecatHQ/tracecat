"""Tests for platform registry startup sync.

This test suite validates:
1. PlatformRegistryReposService CRUD operations
2. Leader election behavior (lock acquisition)
3. Version existence checks (skip sync if version exists and is current)
4. No auto-promotion of existing non-current versions
5. No-downgrade guard during sync
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService

pytestmark = pytest.mark.usefixtures("db")


def _make_manifest(action_names: list[str]) -> dict:
    """Create a test manifest with given action names."""
    actions = {}
    for name in action_names:
        parts = name.rsplit(".", 1)
        namespace = parts[0] if len(parts) > 1 else "test"
        action_name = parts[-1]
        actions[name] = {
            "namespace": namespace,
            "name": action_name,
            "action_type": "udf",
            "description": f"Test action {name}",
            "interface": {"expects": {}, "returns": None},
            "implementation": {
                "type": "udf",
                "url": "test_origin",
                "module": f"test.{namespace}",
                "name": action_name,
            },
        }
    return {"version": "1.0", "actions": actions}


# =============================================================================
# PlatformRegistryReposService Tests
# =============================================================================


@pytest.mark.anyio
async def test_platform_repos_service_create_repository(
    session: AsyncSession,
) -> None:
    """Test creating a platform registry repository."""
    service = PlatformRegistryReposService(session, role=None)

    repo = await service.create_repository(DEFAULT_REGISTRY_ORIGIN)

    assert repo.origin == DEFAULT_REGISTRY_ORIGIN
    assert repo.id is not None
    assert repo.current_version_id is None


@pytest.mark.anyio
async def test_platform_repos_service_get_repository(
    session: AsyncSession,
) -> None:
    """Test getting a platform registry repository by origin."""
    # Create directly
    repo = PlatformRegistryRepository(origin="test_platform_origin")
    session.add(repo)
    await session.commit()

    service = PlatformRegistryReposService(session, role=None)
    found = await service.get_repository("test_platform_origin")

    assert found is not None
    assert found.id == repo.id
    assert found.origin == "test_platform_origin"


@pytest.mark.anyio
async def test_platform_repos_service_get_repository_not_found(
    session: AsyncSession,
) -> None:
    """Test getting a non-existent repository returns None."""
    service = PlatformRegistryReposService(session, role=None)
    found = await service.get_repository("nonexistent_origin")

    assert found is None


@pytest.mark.anyio
async def test_platform_repos_service_get_or_create_existing(
    session: AsyncSession,
) -> None:
    """Test get_or_create returns existing repository."""
    # Create directly
    repo = PlatformRegistryRepository(origin="existing_origin")
    session.add(repo)
    await session.commit()

    service = PlatformRegistryReposService(session, role=None)
    found = await service.get_or_create_repository("existing_origin")

    assert found.id == repo.id


@pytest.mark.anyio
async def test_platform_repos_service_get_or_create_new(
    session: AsyncSession,
) -> None:
    """Test get_or_create creates new repository if not exists."""
    service = PlatformRegistryReposService(session, role=None)
    repo = await service.get_or_create_repository("new_origin")

    assert repo.origin == "new_origin"
    assert repo.id is not None


# =============================================================================
# Platform Startup Sync Job Tests
# =============================================================================


@pytest.mark.anyio
async def test_startup_sync_skips_when_version_already_current(
    session: AsyncSession,
) -> None:
    """Test that startup sync skips when target version is already current."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    # Create platform repo with current version matching target
    repo = PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN)
    session.add(repo)
    await session.flush()

    version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="0.1.0",  # Will be our target version
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.flush()

    repo.current_version_id = version.id
    session.add(repo)
    await session.commit()

    # Mock tracecat_registry.__version__ to return our version
    with patch("tracecat.registry.sync.jobs.tracecat_registry") as mock_registry:
        mock_registry.__version__ = "0.1.0"

        # Should complete without calling sync
        with patch(
            "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
        ) as mock_sync_service:
            await _sync_as_leader(session, "0.1.0")
            # Sync service should not be instantiated
            mock_sync_service.assert_not_called()


@pytest.mark.anyio
async def test_startup_sync_does_not_promote_existing_non_current_version(
    session: AsyncSession,
) -> None:
    """Test that startup sync does NOT auto-promote existing non-current versions."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    # Create platform repo
    repo = PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN)
    session.add(repo)
    await session.flush()

    # Create version 1.0.0 (current)
    version1 = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version1)
    await session.flush()

    # Create version 2.0.0 (exists but NOT current - maybe user rolled back)
    version2 = PlatformRegistryVersion(
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add(version2)
    await session.flush()

    # Set version 1 as current (simulating a rollback scenario)
    repo.current_version_id = version1.id
    session.add(repo)
    await session.commit()

    # Try to sync to version 2.0.0 (which exists but is not current)
    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_service:
        await _sync_as_leader(session, "2.0.0")

        # Should NOT call sync service (version exists)
        mock_sync_service.assert_not_called()

    # Verify current version is still version 1
    await session.refresh(repo)
    assert repo.current_version_id == version1.id


@pytest.mark.anyio
async def test_startup_sync_refuses_downgrade(
    session: AsyncSession,
) -> None:
    """Test that startup sync refuses to downgrade to older version."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    # Create platform repo with version 2.0.0
    repo = PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN)
    session.add(repo)
    await session.flush()

    version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add(version)
    await session.flush()

    repo.current_version_id = version.id
    session.add(repo)
    await session.commit()

    # Try to sync to version 1.0.0 (older)
    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_service:
        await _sync_as_leader(session, "1.0.0")

        # Should NOT call sync service (downgrade refused)
        mock_sync_service.assert_not_called()

    # Verify current version is still 2.0.0
    await session.refresh(repo)
    assert repo.current_version_id == version.id


@pytest.mark.anyio
async def test_startup_sync_calls_sync_for_new_version(
    session: AsyncSession,
) -> None:
    """Test that startup sync calls sync service for new versions."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    # Create platform repo without any versions
    repo = PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN)
    session.add(repo)
    await session.commit()

    # Mock the sync service
    mock_result = AsyncMock()
    mock_result.version_string = "1.0.0"
    mock_result.num_actions = 10

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_cls:
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = mock_result
        mock_sync_cls.return_value = mock_sync_service

        await _sync_as_leader(session, "1.0.0")

        # Sync service should be called with bypass_temporal=True
        mock_sync_service.sync_repository_v2.assert_called_once()
        call_kwargs = mock_sync_service.sync_repository_v2.call_args.kwargs
        assert call_kwargs["target_version"] == "1.0.0"
        assert call_kwargs["bypass_temporal"] is True


@pytest.mark.anyio
async def test_startup_sync_leader_election_non_leader_exits(
    session: AsyncSession,
) -> None:
    """Test that non-leader process exits immediately without syncing."""
    from tracecat.registry.sync.jobs import sync_platform_registry_on_startup

    # Mock try_pg_advisory_lock to return False (lock not acquired)
    with (
        patch(
            "tracecat.registry.sync.jobs.try_pg_advisory_lock", return_value=False
        ) as mock_lock,
        patch("tracecat.registry.sync.jobs._sync_as_leader") as mock_sync,
    ):
        await sync_platform_registry_on_startup()

        # Lock was attempted
        mock_lock.assert_called_once()
        # But sync was NOT called (non-leader)
        mock_sync.assert_not_called()
