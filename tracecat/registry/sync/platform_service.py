"""Platform registry sync service for v2 versioned registry flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.registry.sync.base_service import BaseRegistrySyncService, BaseSyncResult
from tracecat.registry.versions.service import PlatformRegistryVersionsService

PLATFORM_REGISTRY_TARBALL_NAMESPACE = "platform"


class PlatformRegistrySyncError(Exception):
    """Raised when platform registry sync fails."""


@dataclass
class PlatformSyncResult(BaseSyncResult[PlatformRegistryVersion]):
    """Result of a platform registry sync operation."""


class PlatformRegistrySyncService(
    BaseRegistrySyncService[PlatformRegistryRepository, PlatformRegistryVersion]
):
    """Service for orchestrating platform registry sync operations.

    Platform actions are stored in platform-scoped tables (platform_registry_*)
    and queried via UNION ALL with org-scoped tables in list_actions_from_index().
    """

    service_name: ClassVar[str] = "platform_registry_sync"

    @classmethod
    def _versions_service_cls(cls) -> type[PlatformRegistryVersionsService]:
        return PlatformRegistryVersionsService

    @classmethod
    def _result_cls(cls) -> type[PlatformSyncResult]:
        return PlatformSyncResult

    @classmethod
    def _sync_error_cls(cls) -> type[Exception]:
        return PlatformRegistrySyncError

    @classmethod
    def _storage_namespace(cls) -> str:
        return PLATFORM_REGISTRY_TARBALL_NAMESPACE
