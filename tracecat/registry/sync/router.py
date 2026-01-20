"""Router for platform registry sync status endpoints."""

from __future__ import annotations

import tracecat_registry
from fastapi import APIRouter

from tracecat.db.dependencies import AsyncDBSession
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.sync.schemas import PlatformSyncStatusResponse

router = APIRouter(prefix="/registry/platform", tags=["registry-platform"])


@router.get("/sync-status")
async def get_platform_sync_status(
    session: AsyncDBSession,
) -> PlatformSyncStatusResponse:
    """Return platform registry sync status by querying the DB.

    This endpoint checks whether the platform registry has been synced to the
    expected version (from tracecat_registry.__version__). It's useful for:
    - Health checks that need to verify the registry is ready
    - UI status indicators showing sync progress
    - Debugging startup issues with registry synchronization
    """
    expected_version = tracecat_registry.__version__

    repos_service = PlatformRegistryReposService(session, role=None)
    repo = await repos_service.get_repository(DEFAULT_REGISTRY_ORIGIN)

    if repo is None or repo.current_version is None:
        return PlatformSyncStatusResponse(
            synced=False,
            expected_version=expected_version,
            current_version=None,
            synced_at=None,
        )

    return PlatformSyncStatusResponse(
        synced=repo.current_version.version == expected_version,
        expected_version=expected_version,
        current_version=repo.current_version.version,
        synced_at=repo.current_version.created_at,
    )
