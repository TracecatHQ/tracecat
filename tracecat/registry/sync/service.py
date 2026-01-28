"""Registry sync service for v2 versioned registry flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, override

from tracecat.auth.types import Role
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.registry.sync.base_service import BaseRegistrySyncService, BaseSyncResult
from tracecat.registry.versions.service import RegistryVersionsService


class RegistrySyncError(Exception):
    """Raised when registry sync fails."""


@dataclass
class SyncResult(BaseSyncResult[RegistryVersion]):
    """Result of a registry sync operation."""


class RegistrySyncService(BaseRegistrySyncService[RegistryRepository, RegistryVersion]):
    """Service for orchestrating registry sync operations.

    Requires organization context (role must be non-None).
    """

    service_name: ClassVar[str] = "registry_sync"

    @override
    @classmethod
    def _versions_service_cls(cls) -> type[RegistryVersionsService]:
        return RegistryVersionsService

    @override
    @classmethod
    def _result_cls(cls) -> type[SyncResult]:
        return SyncResult

    @override
    @classmethod
    def _sync_error_cls(cls) -> type[Exception]:
        return RegistrySyncError

    @override
    def _get_storage_namespace(self) -> str:
        """Get storage namespace from the organization context."""
        if self.role is None or not isinstance(self.role, Role):
            raise TracecatAuthorizationError(
                "RegistrySyncService requires organization context"
            )
        if self.role.organization_id is None:
            raise TracecatAuthorizationError(
                "RegistrySyncService requires organization_id in role"
            )
        return str(self.role.organization_id)
