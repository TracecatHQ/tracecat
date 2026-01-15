"""Platform registry sync service for v2 versioned registry flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, override

from tracecat.db.models import PlatformRegistryRepository, PlatformRegistryVersion
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
    """Service for orchestrating platform registry sync operations."""

    service_name: ClassVar[str] = "platform_registry_sync"

    @override
    @classmethod
    def _versions_service_cls(cls) -> type[PlatformRegistryVersionsService]:
        return PlatformRegistryVersionsService

    @override
    @classmethod
    def _result_cls(cls) -> type[PlatformSyncResult]:
        return PlatformSyncResult

    @override
    @classmethod
    def _sync_error_cls(cls) -> type[Exception]:
        return PlatformRegistrySyncError

    @override
    @classmethod
    def _storage_namespace(cls) -> str:
        return PLATFORM_REGISTRY_TARBALL_NAMESPACE
