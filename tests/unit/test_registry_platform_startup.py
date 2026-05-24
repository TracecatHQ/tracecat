"""Tests for platform registry startup sync.

This test suite validates:
1. PlatformRegistryReposService CRUD operations
2. Leader election behavior (lock acquisition)
3. Version existence checks (skip sync if version exists and is current)
4. No auto-promotion of existing non-current versions
5. No-downgrade guard during sync
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.registry.constants import (
    DEFAULT_LOCAL_REGISTRY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate

pytestmark = pytest.mark.usefixtures("db")


async def _get_or_create_platform_repo(
    session: AsyncSession,
    origin: str = DEFAULT_REGISTRY_ORIGIN,
) -> PlatformRegistryRepository:
    """Get existing platform repo or create new one.

    The conftest pre-seeds platform registry for executor tests,
    so these tests need to handle the existing repo.
    """
    result = await session.execute(
        select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.origin == origin
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        repo = PlatformRegistryRepository(origin=origin)
        session.add(repo)
        await session.flush()
    return repo


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
    service = PlatformRegistryReposService(session)

    # Use DEFAULT_LOCAL_REGISTRY_ORIGIN since DEFAULT_REGISTRY_ORIGIN is pre-seeded by conftest
    repo = await service.create_repository(
        RegistryRepositoryCreate(origin=DEFAULT_LOCAL_REGISTRY_ORIGIN)
    )

    assert repo.origin == DEFAULT_LOCAL_REGISTRY_ORIGIN
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

    service = PlatformRegistryReposService(session)
    found = await service.get_repository("test_platform_origin")

    assert found is not None
    assert found.id == repo.id
    assert found.origin == "test_platform_origin"


@pytest.mark.anyio
async def test_platform_repos_service_get_repository_not_found(
    session: AsyncSession,
) -> None:
    """Test getting a non-existent repository returns None."""
    service = PlatformRegistryReposService(session)
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

    service = PlatformRegistryReposService(session)
    found = await service.get_or_create_repository("existing_origin")

    assert found.id == repo.id


@pytest.mark.anyio
async def test_platform_repos_service_get_or_create_new(
    session: AsyncSession,
) -> None:
    """Test get_or_create creates new repository if not exists."""
    service = PlatformRegistryReposService(session)
    repo = await service.get_or_create_repository(DEFAULT_LOCAL_REGISTRY_ORIGIN)

    assert repo.origin == DEFAULT_LOCAL_REGISTRY_ORIGIN
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

    # Get or create platform repo (may already exist from conftest seeding)
    repo = await _get_or_create_platform_repo(session)

    # Create or update version 0.1.0 as current
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
        with (
            patch(
                "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
            ) as mock_sync_service,
            patch(
                "tracecat.registry.sync.jobs._schedule_platform_registry_artifact_build"
            ) as schedule_build,
        ):
            await _sync_as_leader(session, "0.1.0")
            # Sync service should not be instantiated
            mock_sync_service.assert_not_called()
            schedule_build.assert_called_once_with(
                "0.1.0",
                promote_version_id=version.id,
                expected_current_version_id=version.id,
            )


@pytest.mark.anyio
async def test_startup_sync_does_not_promote_existing_non_current_version(
    session: AsyncSession,
) -> None:
    """Test that startup sync does NOT auto-promote existing non-current versions."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    # Get or create platform repo (may already exist from conftest seeding)
    repo = await _get_or_create_platform_repo(session)

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
async def test_startup_sync_defers_existing_target_when_no_current_version(
    session: AsyncSession,
) -> None:
    """Test startup sync waits for artifacts before repairing a missing pointer."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    repo = await _get_or_create_platform_repo(session)
    repo.current_version_id = None
    session.add(repo)
    await session.flush()

    version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.commit()

    with (
        patch(
            "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
        ) as mock_sync_service,
        patch(
            "tracecat.registry.sync.jobs._schedule_platform_registry_artifact_build"
        ) as schedule_build,
    ):
        await _sync_as_leader(session, "1.0.0")
        mock_sync_service.assert_not_called()
        schedule_build.assert_called_once_with(
            "1.0.0",
            promote_version_id=version.id,
            expected_current_version_id=None,
        )

    await session.refresh(repo)
    assert repo.current_version_id is None


@pytest.mark.anyio
async def test_startup_sync_refuses_downgrade(
    session: AsyncSession,
) -> None:
    """Test that startup sync refuses to downgrade to older version."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    # Get or create platform repo (may already exist from conftest seeding)
    repo = await _get_or_create_platform_repo(session)

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
    """Test fresh startup sync promotes immediately for first-install UX."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    repo = await _get_or_create_platform_repo(session)
    repo.current_version_id = None
    session.add(repo)
    await session.flush()
    await session.execute(
        delete(PlatformRegistryVersion).where(
            PlatformRegistryVersion.repository_id == repo.id
        )
    )
    await session.commit()

    mock_result = SimpleNamespace(
        version=SimpleNamespace(id=uuid.uuid4()),
        version_string="1.0.0",
        num_actions=0,
        actions=[],
    )

    with (
        patch(
            "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
        ) as mock_sync_cls,
        patch(
            "tracecat.registry.sync.jobs._schedule_platform_registry_artifact_build"
        ) as schedule_build,
    ):
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = mock_result
        mock_sync_cls.return_value = mock_sync_service

        await _sync_as_leader(session, "1.0.0")

        # Sync service should be called with bypass_temporal=True
        mock_sync_service.sync_repository_v2.assert_called_once()
        call_kwargs = mock_sync_service.sync_repository_v2.call_args.kwargs
        assert call_kwargs["target_version"] == "1.0.0"
        assert call_kwargs["bypass_temporal"] is True
        assert call_kwargs["defer_artifact_build"] is True
        assert call_kwargs["promote"] is True
        schedule_build.assert_called_once_with(
            "1.0.0",
            promote_version_id=None,
            expected_current_version_id=None,
        )


@pytest.mark.anyio
async def test_startup_sync_defers_promotion_for_new_upgrade_version(
    session: AsyncSession,
) -> None:
    """Test upgrade startup sync promotes only after artifact upload."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    repo = await _get_or_create_platform_repo(session)
    old_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(old_version)
    await session.flush()
    repo.current_version_id = old_version.id
    session.add(repo)
    await session.commit()

    new_version_id = uuid.uuid4()
    mock_result = SimpleNamespace(
        version=SimpleNamespace(id=new_version_id),
        version_string="2.0.0",
        num_actions=0,
        actions=[],
    )

    with (
        patch(
            "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
        ) as mock_sync_cls,
        patch(
            "tracecat.registry.sync.jobs._schedule_platform_registry_artifact_build"
        ) as schedule_build,
    ):
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = mock_result
        mock_sync_cls.return_value = mock_sync_service

        await _sync_as_leader(session, "2.0.0")

        call_kwargs = mock_sync_service.sync_repository_v2.call_args.kwargs
        assert call_kwargs["defer_artifact_build"] is True
        assert call_kwargs["promote"] is False
        schedule_build.assert_called_once_with(
            "2.0.0",
            promote_version_id=new_version_id,
            expected_current_version_id=old_version.id,
        )


@pytest.mark.anyio
async def test_startup_sync_schedules_artifact_for_resolved_version(
    session: AsyncSession,
) -> None:
    from tracecat.registry.sync.jobs import _sync_as_leader

    repo = await _get_or_create_platform_repo(session)
    repo.current_version_id = None
    session.add(repo)
    await session.flush()
    await session.execute(
        delete(PlatformRegistryVersion).where(
            PlatformRegistryVersion.repository_id == repo.id
        )
    )
    await session.commit()

    mock_result = SimpleNamespace(
        version=SimpleNamespace(id=uuid.uuid4()),
        version_string="1.0.0.dev20260522140000",
        num_actions=0,
        actions=[],
    )

    with (
        patch(
            "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
        ) as mock_sync_cls,
        patch(
            "tracecat.registry.sync.jobs._schedule_platform_registry_artifact_build"
        ) as schedule_build,
    ):
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = mock_result
        mock_sync_cls.return_value = mock_sync_service

        await _sync_as_leader(session, "1.0.0")

        schedule_build.assert_called_once_with(
            "1.0.0.dev20260522140000",
            promote_version_id=None,
            expected_current_version_id=None,
        )


@pytest.mark.anyio
async def test_promote_after_artifact_build_promotes_when_current_matches_guard(
    session: AsyncSession,
) -> None:
    from tracecat.registry.sync.jobs import (
        _promote_platform_registry_version_after_artifact_build,
    )

    repo = await _get_or_create_platform_repo(session)
    old_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    new_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([old_version, new_version])
    await session.flush()
    repo.current_version_id = old_version.id
    session.add(repo)
    await session.commit()

    await _promote_platform_registry_version_after_artifact_build(
        session,
        target_version="2.0.0",
        version_id=new_version.id,
        artifact_uri="s3://test/v2.tar.gz",
        expected_current_version_id=old_version.id,
    )

    await session.refresh(repo)
    assert repo.current_version_id == new_version.id


@pytest.mark.anyio
async def test_promote_after_artifact_build_updates_stored_artifact_uri(
    session: AsyncSession,
) -> None:
    from tracecat.registry.sync.jobs import (
        _promote_platform_registry_version_after_artifact_build,
    )

    repo = await _get_or_create_platform_repo(session)
    old_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    pending_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/stale.tar.gz",
    )
    session.add_all([old_version, pending_version])
    await session.flush()
    repo.current_version_id = old_version.id
    session.add(repo)
    await session.commit()

    artifact_uri = "s3://test/v2/site-packages.squashfs"
    await _promote_platform_registry_version_after_artifact_build(
        session,
        target_version="2.0.0",
        version_id=pending_version.id,
        artifact_uri=artifact_uri,
        expected_current_version_id=old_version.id,
    )

    await session.refresh(repo)
    await session.refresh(pending_version)
    assert repo.current_version_id == pending_version.id
    assert pending_version.tarball_uri == artifact_uri


@pytest.mark.anyio
async def test_promote_after_artifact_build_promotes_collision_version(
    session: AsyncSession,
) -> None:
    from tracecat.registry.sync.jobs import (
        _promote_platform_registry_version_after_artifact_build,
    )

    repo = await _get_or_create_platform_repo(session)
    old_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.2.3",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    collision_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.2.3.dev20260522140000",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/collision.tar.gz",
    )
    session.add_all([old_version, collision_version])
    await session.flush()
    repo.current_version_id = old_version.id
    session.add(repo)
    await session.commit()

    await _promote_platform_registry_version_after_artifact_build(
        session,
        target_version="1.2.3.dev20260522140000",
        version_id=collision_version.id,
        artifact_uri="s3://test/collision.tar.gz",
        expected_current_version_id=old_version.id,
    )

    await session.refresh(repo)
    assert repo.current_version_id == collision_version.id


@pytest.mark.anyio
async def test_promote_after_artifact_build_skips_when_current_changed(
    session: AsyncSession,
) -> None:
    from tracecat.registry.sync.jobs import (
        _promote_platform_registry_version_after_artifact_build,
    )

    repo = await _get_or_create_platform_repo(session)
    old_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    newer_current = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.5.0",
        manifest=_make_manifest(["test.action_v1_5"]),
        tarball_uri="s3://test/v1_5.tar.gz",
    )
    pending_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([old_version, newer_current, pending_version])
    await session.flush()
    repo.current_version_id = newer_current.id
    session.add(repo)
    await session.commit()

    await _promote_platform_registry_version_after_artifact_build(
        session,
        target_version="2.0.0",
        version_id=pending_version.id,
        artifact_uri="s3://test/v2.tar.gz",
        expected_current_version_id=old_version.id,
    )

    await session.refresh(repo)
    assert repo.current_version_id == newer_current.id


@pytest.mark.anyio
async def test_schedule_platform_registry_artifact_build_keeps_task_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.registry.sync import jobs

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_build_platform_registry_artifact(
        target_version: str,
        *,
        promote_version_id: uuid.UUID | None = None,
        expected_current_version_id: uuid.UUID | None = None,
    ) -> None:
        assert target_version == "1.0.0"
        assert promote_version_id is None
        assert expected_current_version_id is None
        started.set()
        await release.wait()

    monkeypatch.setattr(
        jobs,
        "_build_platform_registry_artifact",
        fake_build_platform_registry_artifact,
    )

    jobs._schedule_platform_registry_artifact_build("1.0.0")

    await started.wait()
    tasks = set(jobs._platform_registry_artifact_build_tasks)
    assert len(tasks) == 1

    release.set()
    await asyncio.gather(*tasks)
    assert jobs._platform_registry_artifact_build_tasks == set()


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
