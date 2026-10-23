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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.registry.artifact_keys import get_artifact_s3_prefix
from tracecat.registry.constants import (
    DEFAULT_REGISTRY_ORIGIN,
    PLATFORM_REGISTRY_NAMESPACE,
)
from tracecat.registry.sync.prebuilt import write_prebuilt_registry_manifest
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    registry_content_version,
    registry_manifest_fingerprint,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def prebuilt_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point prebuilt manifest lookups at an empty per-test directory."""
    root = tmp_path / "prebuilt"
    monkeypatch.setattr(
        "tracecat.config.TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR", str(root)
    )
    return root


def _write_prebuilt(root: Path, version: str, manifest: dict) -> str:
    """Bake a prebuilt builtin manifest for ``version`` and return its fingerprint."""
    model = RegistryVersionManifest.model_validate(manifest)
    write_prebuilt_registry_manifest(
        artifact_dir=root
        / get_artifact_s3_prefix(
            organization_id=PLATFORM_REGISTRY_NAMESPACE,
            repository_origin=DEFAULT_REGISTRY_ORIGIN,
            version=version,
        ),
        manifest=model,
    )
    return registry_manifest_fingerprint(model)


async def _reset_platform_versions(session: AsyncSession) -> PlatformRegistryRepository:
    """Return the platform repo with no versions and no current selection."""
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
    return repo


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
async def test_startup_sync_uses_content_version_when_base_row_content_differs(
    session: AsyncSession, prebuilt_root: Path
) -> None:
    """A changed manifest under the same version gets its own immutable row.

    The base row is left untouched, the new content syncs under
    `<version>+content.<fingerprint>`, and it is promoted only after its
    artifact is built.
    """
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

    repo = await _reset_platform_versions(session)
    base_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(base_version)
    await session.flush()
    repo.current_version_id = base_version.id
    session.add(repo)
    await session.commit()

    fingerprint = _write_prebuilt(
        prebuilt_root, "1.0.0", _make_manifest(["test.action_v1", "test.action_v2"])
    )
    content_version = registry_content_version("1.0.0", fingerprint)
    new_version_id = uuid.uuid4()
    mock_result = SimpleNamespace(
        version=SimpleNamespace(id=new_version_id),
        version_string=content_version,
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

        call_kwargs = mock_sync_service.sync_repository_v2.call_args.kwargs
        assert call_kwargs["target_version"] == content_version
        assert call_kwargs["promote"] is False
        assert artifact_build == _ArtifactBuildRequest(
            target_version=content_version,
            promote_version_id=new_version_id,
            expected_current_version_id=base_version.id,
        )

    assert content_version == f"1.0.0+content.{fingerprint[:16]}"


@pytest.mark.anyio
async def test_startup_sync_keeps_base_version_when_prebuilt_content_matches(
    session: AsyncSession, prebuilt_root: Path
) -> None:
    """Same version and same content is already synced; no content version."""
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

    repo = await _reset_platform_versions(session)
    manifest = _make_manifest(["test.action"])
    base_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=manifest,
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(base_version)
    await session.flush()
    repo.current_version_id = base_version.id
    session.add(repo)
    await session.commit()
    _write_prebuilt(prebuilt_root, "1.0.0", manifest)

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_service:
        artifact_build = await _sync_as_leader(session, "1.0.0")
        mock_sync_service.assert_not_called()

    assert artifact_build == _ArtifactBuildRequest(
        target_version="1.0.0",
        promote_version_id=base_version.id,
        expected_current_version_id=base_version.id,
    )


@pytest.mark.anyio
async def test_startup_sync_skips_when_content_version_already_current(
    session: AsyncSession, prebuilt_root: Path
) -> None:
    """Restarting the same image reuses its content row instead of re-syncing."""
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

    repo = await _reset_platform_versions(session)
    image_manifest = _make_manifest(["test.action_v1", "test.action_v2"])
    fingerprint = _write_prebuilt(prebuilt_root, "1.0.0", image_manifest)
    content_version_string = registry_content_version("1.0.0", fingerprint)
    base_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    content_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version=content_version_string,
        manifest=image_manifest,
        tarball_uri="s3://test/content.tar.gz",
    )
    session.add_all([base_version, content_version])
    await session.flush()
    repo.current_version_id = content_version.id
    session.add(repo)
    await session.commit()

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_service:
        artifact_build = await _sync_as_leader(session, "1.0.0")
        mock_sync_service.assert_not_called()

    assert artifact_build == _ArtifactBuildRequest(
        target_version=content_version_string,
        promote_version_id=content_version.id,
        expected_current_version_id=content_version.id,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "artifact_exists", [True, False], ids=["rolled-back", "unbuilt"]
)
async def test_startup_sync_handles_existing_non_current_content_version(
    session: AsyncSession, prebuilt_root: Path, artifact_exists: bool
) -> None:
    """A non-current content row with an artifact is a rollback and stays put.

    Content rows are promoted only after their artifact upload, so one without an
    artifact never finished its first sync: startup retries the build and promotes.
    """
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

    repo = await _reset_platform_versions(session)
    image_manifest = _make_manifest(["test.action_v1", "test.action_v2"])
    fingerprint = _write_prebuilt(prebuilt_root, "1.0.0", image_manifest)
    base_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    content_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version=registry_content_version("1.0.0", fingerprint),
        manifest=image_manifest,
        tarball_uri="s3://test/content.tar.gz",
    )
    session.add_all([base_version, content_version])
    await session.flush()
    repo.current_version_id = base_version.id
    session.add(repo)
    await session.commit()

    with (
        patch(
            "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
        ) as mock_sync_service,
        patch(
            "tracecat.registry.sync.jobs._artifact_exists",
            AsyncMock(return_value=artifact_exists),
        ) as mock_artifact_exists,
    ):
        artifact_build = await _sync_as_leader(session, "1.0.0")
        mock_sync_service.assert_not_called()
        mock_artifact_exists.assert_awaited_once_with("s3://test/content.tar.gz")

    if artifact_exists:
        assert artifact_build is None
    else:
        assert artifact_build == _ArtifactBuildRequest(
            target_version=content_version.version,
            promote_version_id=content_version.id,
            expected_current_version_id=base_version.id,
        )
    await session.refresh(repo)
    assert repo.current_version_id == base_version.id


@pytest.mark.anyio
async def test_startup_sync_does_not_retry_unbuilt_content_version_over_newer_release(
    session: AsyncSession, prebuilt_root: Path
) -> None:
    """An older image never promotes its unbuilt content row over a newer release."""
    from tracecat.registry.sync.jobs import _sync_as_leader

    repo = await _reset_platform_versions(session)
    image_manifest = _make_manifest(["test.action_v1", "test.action_v2"])
    fingerprint = _write_prebuilt(prebuilt_root, "1.0.0", image_manifest)
    base_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    content_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version=registry_content_version("1.0.0", fingerprint),
        manifest=image_manifest,
        tarball_uri="s3://test/content.tar.gz",
    )
    newer_release = PlatformRegistryVersion(
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v3"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([base_version, content_version, newer_release])
    await session.flush()
    repo.current_version_id = newer_release.id
    session.add(repo)
    await session.commit()

    with patch(
        "tracecat.registry.sync.jobs._artifact_exists",
        AsyncMock(return_value=False),
    ) as mock_artifact_exists:
        artifact_build = await _sync_as_leader(session, "1.0.0")
        mock_artifact_exists.assert_not_awaited()

    assert artifact_build is None
    await session.refresh(repo)
    assert repo.current_version_id == newer_release.id


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
async def test_startup_sync_awaits_artifact_build_after_releasing_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tracecat.registry.sync import jobs
    from tracecat.registry.sync.jobs import _ArtifactBuildRequest

    build_started = asyncio.Event()
    release_build = asyncio.Event()
    lock_released = asyncio.Event()
    artifact_build = _ArtifactBuildRequest(target_version="1.0.0")

    async def fake_sync_as_leader(
        session: AsyncSession,
        target_version: str,
    ) -> _ArtifactBuildRequest:
        del session
        assert target_version == "1.0.0"
        return artifact_build

    async def fake_release_lock(session: AsyncSession, lock_key: int) -> None:
        del session
        assert lock_key == jobs.PLATFORM_SYNC_LOCK_KEY
        lock_released.set()

    async def fake_build(
        target_version: str,
        *,
        promote_version_id: uuid.UUID | None = None,
        expected_current_version_id: uuid.UUID | None = None,
    ) -> None:
        assert target_version == "1.0.0"
        assert promote_version_id is None
        assert expected_current_version_id is None
        assert lock_released.is_set()
        build_started.set()
        await release_build.wait()

    monkeypatch.setattr(jobs.tracecat_registry, "__version__", "1.0.0")
    monkeypatch.setattr(jobs, "try_pg_advisory_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(jobs, "_sync_as_leader", fake_sync_as_leader)
    monkeypatch.setattr(jobs, "pg_advisory_unlock", fake_release_lock)
    monkeypatch.setattr(jobs, "_build_platform_registry_artifact", fake_build)

    startup_task = asyncio.create_task(jobs.sync_platform_registry_on_startup())
    await build_started.wait()

    assert lock_released.is_set()
    assert not startup_task.done()

    release_build.set()
    await startup_task


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
