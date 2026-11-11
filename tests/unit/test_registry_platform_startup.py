"""Tests for platform registry startup sync.

This test suite validates:
1. Leader election behavior (lock acquisition)
2. Version existence checks (skip sync if version exists and is current)
3. No auto-promotion of existing non-current versions
4. No-downgrade guard during sync
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import UnboundExecutionError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.registry.constants import (
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.sync.platform_service import PlatformRegistrySyncService

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
# Platform Startup Sync Job Tests
# =============================================================================


@pytest.mark.anyio
async def test_startup_sync_skips_when_version_already_current(
    session: AsyncSession,
) -> None:
    """Test that startup sync skips when target version is already current."""
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

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
        with patch(
            "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
        ) as mock_sync_service:
            artifact_build = await _sync_as_leader(session, "0.1.0")
            # Sync service should not be instantiated
            mock_sync_service.assert_not_called()
            assert artifact_build == _ArtifactBuildRequest(
                target_version="0.1.0",
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
        artifact_build = await _sync_as_leader(session, "2.0.0")

        # Should NOT call sync service (version exists)
        mock_sync_service.assert_not_called()
        assert artifact_build is None

    # Verify current version is still version 1
    await session.refresh(repo)
    assert repo.current_version_id == version1.id


@pytest.mark.anyio
async def test_startup_sync_defers_existing_target_when_no_current_version(
    session: AsyncSession,
) -> None:
    """Test startup sync waits for artifacts before repairing a missing pointer."""
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

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

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_service:
        artifact_build = await _sync_as_leader(session, "1.0.0")
        mock_sync_service.assert_not_called()
        assert artifact_build == _ArtifactBuildRequest(
            target_version="1.0.0",
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
        artifact_build = await _sync_as_leader(session, "1.0.0")

        # Should NOT call sync service (downgrade refused)
        mock_sync_service.assert_not_called()
        assert artifact_build is None

    # Verify current version is still 2.0.0
    await session.refresh(repo)
    assert repo.current_version_id == version.id


@pytest.mark.anyio
async def test_startup_sync_calls_sync_for_new_version(
    session: AsyncSession,
) -> None:
    """Test fresh startup sync promotes immediately for first-install UX."""
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

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

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_cls:
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = mock_result
        mock_sync_cls.return_value = mock_sync_service

        artifact_build = await _sync_as_leader(session, "1.0.0")

        # Sync service should be called with bypass_temporal=True
        mock_sync_service.sync_repository_v2.assert_called_once()
        call_kwargs = mock_sync_service.sync_repository_v2.call_args.kwargs
        assert call_kwargs["target_version"] == "1.0.0"
        assert call_kwargs["bypass_temporal"] is True
        assert call_kwargs["defer_artifact_build"] is True
        assert call_kwargs["promote"] is True
        assert artifact_build == _ArtifactBuildRequest(
            target_version="1.0.0",
            promote_version_id=None,
            expected_current_version_id=None,
        )


@pytest.mark.anyio
async def test_startup_sync_defers_promotion_for_new_upgrade_version(
    session: AsyncSession,
) -> None:
    """Test upgrade startup sync promotes only after artifact upload."""
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

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

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_cls:
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = mock_result
        mock_sync_cls.return_value = mock_sync_service

        artifact_build = await _sync_as_leader(session, "2.0.0")

        call_kwargs = mock_sync_service.sync_repository_v2.call_args.kwargs
        assert call_kwargs["defer_artifact_build"] is True
        assert call_kwargs["promote"] is False
        assert artifact_build == _ArtifactBuildRequest(
            target_version="2.0.0",
            promote_version_id=new_version_id,
            expected_current_version_id=old_version.id,
        )


@pytest.mark.anyio
async def test_startup_sync_returns_artifact_build_for_resolved_version(
    session: AsyncSession,
) -> None:
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

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
        version_string="1.0.0+collision.20260522140000.123456",
        num_actions=0,
        actions=[],
    )

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_cls:
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = mock_result
        mock_sync_cls.return_value = mock_sync_service

        artifact_build = await _sync_as_leader(session, "1.0.0")

        assert artifact_build == _ArtifactBuildRequest(
            target_version="1.0.0+collision.20260522140000.123456",
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
        version="1.2.3+collision.20260522140000.123456",
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
        target_version="1.2.3+collision.20260522140000.123456",
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
async def test_artifact_build_holds_no_db_connection_before_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build runs without a DB connection; promotion opens one afterwards.

    A transaction left idle across a multi-minute build gets its connection
    closed by a pooler or server timeout, and promotion then fails on it.
    """
    from tracecat.registry.sync import jobs
    from tracecat.registry.sync.base_service import ArtifactsBuildResult

    events: list[str] = []

    @asynccontextmanager
    async def fake_session_cm() -> AsyncGenerator[object]:
        events.append("session")
        yield object()

    async def fake_build(
        self: PlatformRegistrySyncService, **kwargs: object
    ) -> ArtifactsBuildResult:
        with pytest.raises(UnboundExecutionError):
            await self.session.execute(select(1))
        events.append("build")
        return ArtifactsBuildResult(artifact_uri="s3://test/v2.tar.gz")

    async def fake_promote(session: object, **kwargs: object) -> None:
        events.append("promote")

    monkeypatch.setattr(
        jobs, "get_async_session_bypass_rls_context_manager", fake_session_cm
    )
    monkeypatch.setattr(
        PlatformRegistrySyncService, "_build_and_upload_artifacts", fake_build
    )
    monkeypatch.setattr(
        jobs, "_promote_platform_registry_version_after_artifact_build", fake_promote
    )

    await jobs._build_platform_registry_artifact(
        "2.0.0",
        promote_version_id=uuid.uuid4(),
        expected_current_version_id=uuid.uuid4(),
    )

    assert events == ["build", "session", "promote"]


def _isolate_platform_sync_lock(monkeypatch: pytest.MonkeyPatch) -> int:
    """Use a per-test key; the lock database is shared across xdist workers."""
    from tracecat.registry.sync import jobs

    key = uuid.uuid4().int % (2**63)
    monkeypatch.setattr(jobs, "PLATFORM_SYNC_LOCK_KEY", key)
    return key


async def _lock_is_free(key: int) -> bool:
    from tracecat.db.engine import get_async_engine
    from tracecat.db.locks import hold_pg_advisory_xact_lock

    async with hold_pg_advisory_xact_lock(
        get_async_engine(), key, wait=False
    ) as holder:
        return holder is not None


@pytest.mark.anyio
async def test_startup_sync_awaits_artifact_build_after_releasing_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.registry.sync import jobs
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest

    lock_key = _isolate_platform_sync_lock(monkeypatch)
    build_started = asyncio.Event()
    release_build = asyncio.Event()
    artifact_build = _ArtifactBuildRequest(target_version="1.0.0")

    async def fake_sync_as_leader(
        session: AsyncSession,
        target_version: str,
    ) -> _ArtifactBuildRequest:
        assert target_version == "1.0.0"
        # The leader keeps the lock across its own commits.
        await session.commit()
        assert not await _lock_is_free(lock_key)
        return artifact_build

    async def fake_build(
        target_version: str,
        *,
        promote_version_id: uuid.UUID | None = None,
        expected_current_version_id: uuid.UUID | None = None,
    ) -> None:
        assert target_version == "1.0.0"
        assert promote_version_id is None
        assert expected_current_version_id is None
        assert await _lock_is_free(lock_key)
        build_started.set()
        await release_build.wait()

    monkeypatch.setattr(jobs.tracecat_registry, "__version__", "1.0.0")
    monkeypatch.setattr(jobs, "_sync_as_leader", fake_sync_as_leader)
    monkeypatch.setattr(jobs, "_build_platform_registry_artifact", fake_build)

    startup_task = asyncio.create_task(jobs.sync_platform_registry_on_startup())
    await asyncio.wait_for(build_started.wait(), timeout=10)

    assert not startup_task.done()

    release_build.set()
    await startup_task


@pytest.mark.anyio
async def test_startup_sync_leader_election_non_leader_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process that finds the leader lock held exits without syncing."""
    from tracecat.db.engine import get_async_engine
    from tracecat.db.locks import hold_pg_advisory_xact_lock
    from tracecat.registry.sync.jobs import sync_platform_registry_on_startup

    lock_key = _isolate_platform_sync_lock(monkeypatch)
    with patch("tracecat.registry.sync.jobs._sync_as_leader") as mock_sync:
        async with hold_pg_advisory_xact_lock(get_async_engine(), lock_key):
            await sync_platform_registry_on_startup()
        mock_sync.assert_not_called()
