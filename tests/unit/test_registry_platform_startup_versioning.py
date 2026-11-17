"""Tests for platform registry startup version selection.

Startup sync does not order versions. A never-seen version is synced and
promoted even when it sorts below the current one, and a seen version that is
not current is never re-promoted.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import PlatformRegistryRepository, PlatformRegistryVersion
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.sync.jobs import _ArtifactBuildRequest, _sync_as_leader

pytestmark = pytest.mark.usefixtures("db")

ALPHA_VERSION = "1.5.0-alpha.5"
# Non-release images stamp 1.0.0.post<commit time><NN><commit id>, which
# PEP 440 sorts below any 1.5.0 pre-release.
MAIN_STAMP_VERSION = "1.0.0.post1794932427000227424"


async def _platform_repo(session: AsyncSession) -> PlatformRegistryRepository:
    result = await session.execute(
        select(PlatformRegistryRepository).where(
            PlatformRegistryRepository.origin == DEFAULT_REGISTRY_ORIGIN
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        repo = PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN)
        session.add(repo)
        await session.flush()
    return repo


async def _add_version(
    session: AsyncSession, repo: PlatformRegistryRepository, version: str
) -> PlatformRegistryVersion:
    row = PlatformRegistryVersion(
        repository_id=repo.id,
        version=version,
        manifest={"version": "1.0", "actions": {}},
        tarball_uri=f"s3://test/{version}.tar.gz",
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.anyio
async def test_startup_sync_promotes_main_stamp_after_alpha(
    session: AsyncSession,
) -> None:
    """A main build deployed after an alpha release is synced and promoted."""
    repo = await _platform_repo(session)
    alpha = await _add_version(session, repo, ALPHA_VERSION)
    repo.current_version_id = alpha.id
    await session.commit()

    new_version_id = uuid.uuid4()
    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_cls:
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_repository_v2.return_value = SimpleNamespace(
            version=SimpleNamespace(id=new_version_id),
            version_string=MAIN_STAMP_VERSION,
            num_actions=0,
            actions=[],
        )
        mock_sync_cls.return_value = mock_sync_service

        artifact_build = await _sync_as_leader(session, MAIN_STAMP_VERSION)

    mock_sync_service.sync_repository_v2.assert_awaited_once()
    call_kwargs = mock_sync_service.sync_repository_v2.call_args.kwargs
    assert call_kwargs["target_version"] == MAIN_STAMP_VERSION
    assert call_kwargs["promote"] is False
    assert artifact_build == _ArtifactBuildRequest(
        target_version=MAIN_STAMP_VERSION,
        promote_version_id=new_version_id,
        expected_current_version_id=alpha.id,
    )


@pytest.mark.anyio
async def test_startup_sync_does_not_repromote_seen_older_version(
    session: AsyncSession,
) -> None:
    """An old pod restarting mid-rollout must not switch back to its version."""
    repo = await _platform_repo(session)
    await _add_version(session, repo, ALPHA_VERSION)
    main_stamp = await _add_version(session, repo, MAIN_STAMP_VERSION)
    repo.current_version_id = main_stamp.id
    await session.commit()

    with patch(
        "tracecat.registry.sync.jobs.PlatformRegistrySyncService"
    ) as mock_sync_cls:
        artifact_build = await _sync_as_leader(session, ALPHA_VERSION)

    mock_sync_cls.assert_not_called()
    assert artifact_build is None
    await session.refresh(repo)
    assert repo.current_version_id == main_stamp.id
